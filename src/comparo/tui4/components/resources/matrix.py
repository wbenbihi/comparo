"""Matrix overview with authored axes and context-compiled cases."""

from collections.abc import Mapping
from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Static

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation
from comparo.tui4.models.resource_presenters import MatrixOverview
from comparo.tui4.models.resource_presenters import compiled_exact_fields
from comparo.tui4.models.resource_presenters import matrix_overview
from comparo.tui4.theme import style

from .base import BaseKindResourceDetails
from .shared import ResourceSettingList
from .shared import resolution_status


class MatrixContextStrip(Horizontal):
    """Compact authored mode, combination, dimensions, and compiled-case facts."""

    def __init__(self, overview: MatrixOverview, planned_cases: int | None) -> None:
        """Build semantic badges without coupling the component to Explorer state."""
        axis_count = len(overview.axes)
        children = [
            Badge(overview.mode, tone=BadgeTone.WARNING, classes="matrix-context-badge"),
            Badge(overview.combine, tone=BadgeTone.NEUTRAL, classes="matrix-context-badge"),
        ]
        if axis_count:
            children.append(
                Badge(
                    f"{axis_count} {'axis' if axis_count == 1 else 'axes'}",
                    tone=BadgeTone.INFO,
                    classes="matrix-context-badge",
                )
            )
        if planned_cases is not None:
            children.append(
                Badge(
                    f"{planned_cases} {'case' if planned_cases == 1 else 'cases'}",
                    tone=BadgeTone.ACTIVE,
                    classes="matrix-context-badge",
                )
            )
        super().__init__(*children, classes="matrix-context-strip")


class MatrixDefinitionBody(Vertical):
    """Axis-oriented Matrix declaration using the shared setting renderer."""

    def __init__(
        self,
        overview: MatrixOverview,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize from the pure Matrix projection."""
        super().__init__(classes="matrix-section-body")
        self._overview = overview
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render each axis and every ordered option without local expansion logic."""
        if not self._overview.axes:
            yield Hint("This Matrix uses explicit cases instead of declared axes.")
            return
        for axis in self._overview.axes:
            count = len(axis.options)
            yield Separator(
                label=f"{axis.id} · {count} {'option' if count == 1 else 'options'}",
                align="left",
                classes="tight",
            )
            yield ResourceSettingList(
                tuple(option.value for option in axis.options),
                empty="This axis has no options.",
                resolved=self._resolved,
            )


class MatrixCasesBody(Vertical):
    """Explicit/include/exclude Matrix controls in authored order."""

    def __init__(
        self,
        overview: MatrixOverview,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize from the pure Matrix projection."""
        super().__init__(classes="matrix-section-body")
        self._overview = overview
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render case values grouped by their declaration semantics."""
        for source, title in (("cases", "Explicit cases"), ("include", "Included cases")):
            cases = tuple(case for case in self._overview.cases if case.source == source)
            if not cases:
                continue
            yield Separator(label=title, align="left", classes="tight")
            yield ResourceSettingList(
                tuple(case.values for case in cases),
                empty="",
                resolved=self._resolved,
            )
        if self._overview.exclusions:
            yield Separator(label="Excluded case IDs", align="left", classes="tight")
            yield Static(
                Text("  ").join(
                    Text(case_id, style("warn")) for case_id in self._overview.exclusions
                ),
                classes="matrix-exclusions",
            )
        if not self._overview.cases and not self._overview.exclusions:
            yield Hint("No explicit, included, or excluded cases are declared.")


class MatrixPlanBody(Vertical):
    """Compiled Matrix case IDs qualified by the exact Compare subject."""

    def __init__(self, resource: BaseKindResourceDetails) -> None:
        """Retain only the common immutable detail state."""
        super().__init__(classes="matrix-section-body")
        self._resource = resource.resource

    @override
    def compose(self) -> ComposeResult:
        """Render the planner-owned case list or a truthful context hint."""
        inspection = self._resource.resolved
        plan = (
            inspection.value.matrix_plan
            if self._resource.overview_mode is ResourceOverviewMode.RESOLVED
            and inspection.value is not None
            else None
        )
        if plan is None:
            if self._resource.overview_mode is not ResourceOverviewMode.RESOLVED:
                yield Hint(
                    "Press r to compile cases in the selected Environment and consumer scope."
                )
            elif inspection.phase is InspectionPhase.LOADING:
                yield Hint("Compiling Matrix cases for the selected execution context…")
            elif inspection.phase is InspectionPhase.FAILED:
                yield Hint(f"Matrix case compilation failed: {inspection.message}")
            elif inspection.phase is InspectionPhase.UNAVAILABLE:
                yield Hint(inspection.message or "Matrix case compilation is unavailable.")
            else:
                yield Hint("This Matrix is not attached under one unambiguous binding.")
            return
        subject = f"{plan.subject.kind}/{plan.subject.resource_id}"
        yield Static(
            Text.assemble(
                ("Binding  ", style("muted")),
                (plan.binding, style("kind.matrix")),
                ("   Subject  ", style("muted")),
                (subject, style("fg")),
            ),
            classes="matrix-plan-context",
        )
        yield Table(
            columns=(Column("ordinal", "#", width=4), Column("case", "Compiled case ID")),
            records=tuple(
                Record(case_id, (str(index), case_id))
                for index, case_id in enumerate(plan.cases, start=1)
            ),
            classes="matrix-plan-table",
        )


class MatrixUsageBody(Vertical):
    """Compact consumer list; the full directional topology remains available on g."""

    def __init__(self, overview: MatrixOverview) -> None:
        """Initialize from the pure Matrix projection."""
        super().__init__(classes="matrix-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render executable consumers with their attachment paths."""
        if not self._overview.consumers:
            yield Hint("This Matrix has no declared consumers.")
            return
        yield Table(
            columns=(Column("kind", "Kind", width=12), Column("resource", "Consumer")),
            records=tuple(
                Record(
                    f"consumer:{index}",
                    (item.target_kind, item.target_id),
                )
                for index, item in enumerate(self._overview.consumers)
            ),
            classes="matrix-usage-table",
        )
        yield Hint("Press g for the complete directional dependency graph.")


class MatrixResourceDetails(BaseKindResourceDetails):
    """Matrix declaration and context-compiled case inspection."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Matrix"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render one continuous Matrix document in raw and resolved modes."""
        overview = matrix_overview(self.entry)
        resolved = self._resolved_fields()
        plan = (
            self.resource.resolved.value.matrix_plan
            if self.resource.overview_mode is ResourceOverviewMode.RESOLVED
            and self.resource.resolved.phase is InspectionPhase.READY
            and self.resource.resolved.value is not None
            else None
        )
        yield MatrixContextStrip(overview, None if plan is None else len(plan.cases))
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        yield Separator(label="Definition", align="left", classes="resource-section-heading")
        yield MatrixDefinitionBody(overview, resolved)
        yield Separator(label="Cases", align="left", classes="resource-section-heading")
        yield MatrixCasesBody(overview, resolved)
        yield Separator(label="Execution plan", align="left", classes="resource-section-heading")
        yield MatrixPlanBody(self)
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield MatrixUsageBody(overview)

    def _resolved_fields(self) -> dict[tuple[str, ...], ResolvedResourceField]:
        """Index exact structural values and masked resolution overlays."""
        if self.resource.overview_mode is not ResourceOverviewMode.RESOLVED:
            return {}
        fields = {field.path: field for field in compiled_exact_fields(self.entry)}
        inspection = self.resource.resolved
        if inspection.phase is InspectionPhase.READY and inspection.value is not None:
            fields.update((field.path, field) for field in inspection.value.fields)
        return fields


__all__ = [
    "MatrixCasesBody",
    "MatrixContextStrip",
    "MatrixDefinitionBody",
    "MatrixPlanBody",
    "MatrixResourceDetails",
    "MatrixUsageBody",
]
