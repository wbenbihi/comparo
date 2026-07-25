"""Reusable advanced Compare controls over typed planning and execution overrides."""

from __future__ import annotations

from typing import cast
from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Input
from textual.widgets import Label
from textual.widgets import Select
from textual.widgets import Static
from textual.widgets import Switch

from comparo.application.planning import ValueNamespace
from comparo.tui.components.compare_input import FailFastChoice
from comparo.tui.components.compare_input import fail_fast_choice
from comparo.tui.components.compare_input import format_budgets
from comparo.tui.components.compare_input import format_matrices
from comparo.tui.components.compare_input import format_value_resources
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import TuiInteractionLimits

ADVANCED_INPUT_IDS = (
    "compare-draft-matrices",
    "compare-draft-trials",
    "compare-draft-attempts",
    "compare-draft-budgets",
    "compare-draft-parameters",
    "compare-draft-variables",
    "compare-draft-secrets",
)


@final
class CompareAdvancedOptions(Vertical):
    """Shared bounded controls for optional planner overrides and mutation approval."""

    def __init__(
        self,
        draft: CompareDraft,
        *,
        limits: TuiInteractionLimits,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize all advanced controls from one typed draft."""
        super().__init__(id=id, classes=classes)
        if type(draft) is not CompareDraft:
            raise TypeError("advanced Compare options require CompareDraft")
        if type(limits) is not TuiInteractionLimits:
            raise TypeError("advanced Compare options require TuiInteractionLimits")
        self._draft = draft
        self._limits = limits

    @override
    def compose(self) -> ComposeResult:
        """Compose bounded adapter fields without owning planner semantics."""
        yield Static("Advanced run options", classes="compare-advanced-title", markup=False)
        yield _option_input(
            "Matrices",
            "compare-draft-matrices",
            format_matrices(self._draft.matrices),
            "region=regions, tenant=tenants",
            maximum=self._limits.maximum_input_characters,
        )
        yield _option_input(
            "Trials",
            "compare-draft-trials",
            _optional_integer(self._draft.trials),
            "Inherit",
            maximum=self._limits.maximum_input_characters,
        )
        yield _option_input(
            "Maximum attempts",
            "compare-draft-attempts",
            _optional_integer(self._draft.maximum_attempts),
            "Inherit",
            maximum=self._limits.maximum_input_characters,
        )
        yield _option_row(
            "Fail fast",
            Select(
                (
                    ("Inherit", FailFastChoice.INHERIT),
                    ("Enabled", FailFastChoice.ENABLED),
                    ("Disabled", FailFastChoice.DISABLED),
                ),
                value=fail_fast_choice(self._draft.fail_fast),
                allow_blank=False,
                id="compare-draft-fail-fast",
            ),
        )
        yield _option_input(
            "Planning budgets",
            "compare-draft-budgets",
            format_budgets(self._draft.budgets),
            "concurrency=4, attempts=20",
            maximum=self._limits.maximum_input_characters,
        )
        yield _option_input(
            "Parameter Values",
            "compare-draft-parameters",
            format_value_resources(self._draft.value_resources, ValueNamespace.PARAMETERS),
            "Value/params, staging=Value/staging-params",
            maximum=self._limits.maximum_input_characters,
        )
        yield _option_input(
            "Variable Values",
            "compare-draft-variables",
            format_value_resources(self._draft.value_resources, ValueNamespace.VARIABLES),
            "Value/variables",
            maximum=self._limits.maximum_input_characters,
        )
        yield _option_input(
            "Secret Values",
            "compare-draft-secrets",
            format_value_resources(self._draft.value_resources, ValueNamespace.SECRETS),
            "Value/secrets, prod=Value/prod-secrets",
            maximum=self._limits.maximum_input_characters,
        )
        yield _option_row(
            "Approve mutations",
            Switch(
                value=self._draft.approve_mutations,
                id="compare-draft-approve-mutations",
            ),
        )
        yield Static("", id="compare-draft-validation", markup=False)

    def show_draft(self, draft: CompareDraft) -> None:
        """Update mounted advanced controls from one typed controller draft."""
        if type(draft) is not CompareDraft:
            raise TypeError("advanced Compare options require CompareDraft")
        self._draft = draft
        if not self.is_mounted:
            return
        values = (
            ("#compare-draft-matrices", format_matrices(draft.matrices)),
            ("#compare-draft-trials", _optional_integer(draft.trials)),
            ("#compare-draft-attempts", _optional_integer(draft.maximum_attempts)),
            ("#compare-draft-budgets", format_budgets(draft.budgets)),
            (
                "#compare-draft-parameters",
                format_value_resources(draft.value_resources, ValueNamespace.PARAMETERS),
            ),
            (
                "#compare-draft-variables",
                format_value_resources(draft.value_resources, ValueNamespace.VARIABLES),
            ),
            (
                "#compare-draft-secrets",
                format_value_resources(draft.value_resources, ValueNamespace.SECRETS),
            ),
        )
        for selector, value in values:
            self.query_one(selector, Input).value = value
        self.query_one("#compare-draft-fail-fast", Select).value = fail_fast_choice(draft.fail_fast)
        self.query_one("#compare-draft-approve-mutations", Switch).value = draft.approve_mutations

    @property
    def fail_fast(self) -> FailFastChoice:
        """Return the exact closed fail-fast control value."""
        if not self.is_mounted:
            return fail_fast_choice(self._draft.fail_fast)
        return cast(
            "FailFastChoice",
            self.query_one("#compare-draft-fail-fast", Select).value,
        )

    @property
    def approve_mutations(self) -> bool:
        """Return the explicit per-run mutation approval control value."""
        if not self.is_mounted:
            return self._draft.approve_mutations
        return self.query_one("#compare-draft-approve-mutations", Switch).value

    def show_validation_code(self, code: str) -> None:
        """Render only a bounded value-free diagnostic code."""
        if self.is_mounted:
            self.query_one("#compare-draft-validation", Static).update(code)


def _option_input(
    label: str,
    input_id: str,
    value: str,
    placeholder: str,
    *,
    maximum: int,
) -> Horizontal:
    return _option_row(
        label,
        Input(
            value=value,
            placeholder=placeholder,
            max_length=maximum,
            id=input_id,
            classes="compare-advanced-input",
        ),
    )


def _option_row(label: str, control: Input | Select[object] | Switch) -> Horizontal:
    return Horizontal(
        Label(label, classes="compare-advanced-label", markup=False),
        control,
        classes="compare-advanced-row",
    )


def _optional_integer(value: int | None) -> str:
    return "" if value is None else str(value)


__all__ = ("ADVANCED_INPUT_IDS", "CompareAdvancedOptions")
