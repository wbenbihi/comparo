"""Editable Matrix-case selection over the planner's exact case identities."""

from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive

from comparo.application import MatrixOverride
from comparo.application import PlanReviewInspection
from comparo.tui4.components.choicemenu import ChoiceMenu
from comparo.tui4.components.menu import MenuItem
from comparo.tui4.components.selectiongroup import SelectionGroup
from comparo.tui4.components.typography import Caption


class VariantChoices(Vertical):
    """Keep selected cases stable while preview counts are refreshed."""

    axes: reactive[tuple[tuple[str, str, tuple[str, ...]], ...]] = reactive((), recompose=True)
    DEFAULT_CSS = """
    VariantChoices { height: auto; }
    VariantChoices ChoiceMenu { height: auto; max-height: 6; }
    VariantChoices Caption { height: auto; }
    """

    class Changed(Message):
        """The user changed a case filter."""

    def __init__(self, *, id: str | None = None) -> None:
        """Initialize inherited case selection."""
        super().__init__(id=id)
        self.selections: dict[str, tuple[str, ...]] = {}

    @override
    def compose(self) -> ComposeResult:
        """Render actual planner cases, with no placeholder variant controls."""
        if not self.axes:
            yield Caption("No Matrix cases in the current scope.", markup=False)
        for index, (binding, matrix, cases) in enumerate(self.axes):
            yield SelectionGroup(
                f"▦ {matrix} · {len(cases)} cases",
                ChoiceMenu(
                    *(MenuItem(case, case) for case in cases),
                    selected=self.selections.get(binding, cases),
                    multiple=True,
                    id=f"variant-{index}",
                ),
                dependent=True,
            )

    @property
    def overrides(self) -> tuple[MatrixOverride, ...]:
        """Lower explicit filters while an untouched axis continues to inherit."""
        return tuple(
            MatrixOverride(binding=binding, matrix=matrix, cases=self.selections[binding])
            for binding, matrix, cases in self.axes
            if binding in self.selections and self.selections[binding] != cases
        )

    def load_review(self, review: PlanReviewInspection) -> None:
        """Retain the complete case list when a filtered preview returns fewer cases."""
        old = {(binding, matrix): cases for binding, matrix, cases in self.axes}
        new = {}
        for item in review.plan.slices:
            for axis in item.matrices:
                key = (axis.binding, axis.matrix.resource_id)
                new[key] = tuple(dict.fromkeys((*old.get(key, ()), *axis.cases)))
        self.axes = tuple((binding, matrix, cases) for (binding, matrix), cases in new.items())

    def reset(self) -> None:
        """Invalidate case filters when their subject/profile context changes."""
        self.selections.clear()
        self.axes = ()

    @on(ChoiceMenu.Changed)
    def _changed(self, event: ChoiceMenu.Changed) -> None:
        event.stop()
        index = int((event.menu.id or "").removeprefix("variant-"))
        self.selections[self.axes[index][0]] = event.selected
        self.post_message(self.Changed())
