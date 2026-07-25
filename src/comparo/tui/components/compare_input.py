"""Bounded value-free translation from Compare controls to typed application options."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

import msgspec

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.planning import BudgetOverride
from comparo.application.planning import BudgetResource
from comparo.application.planning import MatrixOverride
from comparo.application.planning import ValueNamespace
from comparo.application.planning import ValueResourceOverride
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareDraftValidationCode
from comparo.tui.contracts import CompareMode
from comparo.tui.contracts import TuiInteractionLimits


class FailFastChoice(StrEnum):
    """The explicit three-state fail-fast control vocabulary."""

    INHERIT = "inherit"
    ENABLED = "enabled"
    DISABLED = "disabled"


class _InputLimitError(ValueError):
    """Internal control-flow marker for a caller-owned input ceiling."""


class _DraftValidationError(ValueError):
    """Internal value-free marker carrying one closed validation code."""

    def __init__(self, code: CompareDraftValidationCode) -> None:
        super().__init__(code.value)
        self.code = code


def parse_compare_draft(
    fallback: CompareDraft,
    *,
    limits: TuiInteractionLimits,
    mode: CompareMode,
    subjects: str,
    environments: str,
    baseline: str,
    candidates: str,
    execution_profile: str,
    matrices: str,
    trials: str,
    maximum_attempts: str,
    fail_fast: FailFastChoice,
    budgets: str,
    parameters: str,
    variables: str,
    secrets: str,
    approve_mutations: bool,
) -> CompareDraft:
    """Return a typed draft or a value-free invalid marker retaining no authored text."""
    if (
        type(fallback) is not CompareDraft
        or type(limits) is not TuiInteractionLimits
        or type(mode) is not CompareMode
    ):
        raise TypeError("Compare input parsing requires typed draft and mode")
    if type(fail_fast) is not FailFastChoice or type(approve_mutations) is not bool:
        return _invalid(fallback, CompareDraftValidationCode.INVALID_COMBINATION)
    fields = (
        subjects,
        environments,
        baseline,
        candidates,
        execution_profile,
        matrices,
        trials,
        maximum_attempts,
        budgets,
        parameters,
        variables,
        secrets,
    )
    if any(
        type(value) is not str or len(value) > limits.maximum_input_characters for value in fields
    ):
        return _invalid(fallback, CompareDraftValidationCode.INVALID_LIMIT)
    try:
        selections = _validated(
            lambda: (
                _selection_list(subjects, limits),
                _selection_list(environments, limits),
                _optional_selection(baseline, limits),
                _selection_list(candidates, limits),
                _optional_selection(execution_profile, limits),
            ),
            CompareDraftValidationCode.INVALID_SELECTION,
        )
        parsed_matrices = _validated(
            lambda: _matrices(matrices, limits),
            CompareDraftValidationCode.INVALID_MATRIX,
        )
        parsed_trials, parsed_attempts = _validated(
            lambda: (_positive_integer(trials), _positive_integer(maximum_attempts)),
            CompareDraftValidationCode.INVALID_LIMIT,
        )
        parsed_budgets = _validated(
            lambda: _budgets(budgets, limits),
            CompareDraftValidationCode.INVALID_BUDGET,
        )
        value_resources = _validated(
            lambda: (
                *_value_resources(parameters, ValueNamespace.PARAMETERS, limits),
                *_value_resources(variables, ValueNamespace.VARIABLES, limits),
                *_value_resources(secrets, ValueNamespace.SECRETS, limits),
            ),
            CompareDraftValidationCode.INVALID_VALUE_RESOURCE,
        )
    except _DraftValidationError as error:
        return _invalid(fallback, error.code)
    parsed_subjects, parsed_environments, parsed_baseline, parsed_candidates, parsed_profile = (
        selections
    )
    try:
        return CompareDraft(
            mode=mode,
            subjects=parsed_subjects,
            environments=parsed_environments,
            baseline=parsed_baseline,
            candidates=parsed_candidates,
            execution_profile=parsed_profile,
            matrices=parsed_matrices,
            trials=parsed_trials,
            maximum_attempts=parsed_attempts,
            fail_fast=_fail_fast(fail_fast),
            budgets=parsed_budgets,
            value_resources=value_resources,
            approve_mutations=approve_mutations,
        )
    except (TypeError, ValueError):
        return _invalid(fallback, CompareDraftValidationCode.INVALID_COMBINATION)


def validation_diagnostics(draft: CompareDraft) -> tuple[ApplicationDiagnostic, ...]:
    """Project an invalid marker to one safe application diagnostic."""
    if draft.validation_code is None:
        return ()
    return (
        ApplicationDiagnostic(
            code=f"tui.compare.input.{draft.validation_code.value}",
            severity="error",
            stage="invocation",
        ),
    )


def format_selections(values: tuple[str, ...]) -> str:
    """Format typed selections for the bounded comma-separated adapter control."""
    return ", ".join(values)


def format_matrices(values: tuple[MatrixOverride, ...]) -> str:
    """Format typed Matrix bindings without interpreting them."""
    return ", ".join(f"{item.binding}={item.matrix}" for item in values)


def format_budgets(values: tuple[BudgetOverride, ...]) -> str:
    """Format typed budget overrides without changing resource semantics."""
    return ", ".join(f"{item.resource.value}={item.limit}" for item in values)


def format_value_resources(
    values: tuple[ValueResourceOverride, ...],
    namespace: ValueNamespace,
) -> str:
    """Format one typed Value namespace using optional Environment prefixes."""
    return ", ".join(
        item.resource if item.environment is None else f"{item.environment}={item.resource}"
        for item in values
        if item.namespace is namespace
    )


def fail_fast_choice(value: bool | None) -> FailFastChoice:
    """Translate the typed optional Boolean to the closed control vocabulary."""
    if value is None:
        return FailFastChoice.INHERIT
    return FailFastChoice.ENABLED if value else FailFastChoice.DISABLED


def _selection_list(value: str, limits: TuiInteractionLimits) -> tuple[str, ...]:
    values = _items(value, limits)
    if any(len(item) > 63 for item in values):
        raise ValueError("selection is outside the bounded adapter grammar")
    return values


def _optional_selection(value: str, limits: TuiInteractionLimits) -> str | None:
    values = _selection_list(value, limits)
    if len(values) > 1:
        raise ValueError("single selection expected")
    return None if not values else values[0]


def _matrices(value: str, limits: TuiInteractionLimits) -> tuple[MatrixOverride, ...]:
    return tuple(
        MatrixOverride(binding=left, matrix=right)
        for left, right in (_assignment(item) for item in _items(value, limits))
    )


def _budgets(value: str, limits: TuiInteractionLimits) -> tuple[BudgetOverride, ...]:
    return tuple(
        BudgetOverride(resource=BudgetResource(left), limit=_required_integer(right))
        for left, right in (_assignment(item) for item in _items(value, limits))
    )


def _value_resources(
    value: str,
    namespace: ValueNamespace,
    limits: TuiInteractionLimits,
) -> tuple[ValueResourceOverride, ...]:
    results = []
    for item in _items(value, limits):
        if "=" in item:
            environment, resource = _assignment(item)
        else:
            environment, resource = None, item
        results.append(
            ValueResourceOverride(
                namespace=namespace,
                environment=environment,
                resource=resource,
            )
        )
    return tuple(results)


def _items(value: str, limits: TuiInteractionLimits) -> tuple[str, ...]:
    if not value.strip():
        return ()
    items = tuple(item.strip() for item in value.split(","))
    if len(items) > limits.maximum_input_items:
        raise _InputLimitError
    if any(not item for item in items):
        raise ValueError("input is outside the adapter grammar")
    return items


def _validated[T](
    parser: Callable[[], T],
    invalid_code: CompareDraftValidationCode,
) -> T:
    try:
        return parser()
    except _InputLimitError as error:
        raise _DraftValidationError(CompareDraftValidationCode.INVALID_LIMIT) from error
    except (TypeError, ValueError) as error:
        raise _DraftValidationError(invalid_code) from error


def _assignment(value: str) -> tuple[str, str]:
    if value.count("=") != 1:
        raise ValueError("assignment expected")
    left, right = (item.strip() for item in value.split("=", 1))
    if not left or not right or len(left) > 63 or len(right) > 63:
        raise ValueError("assignment is outside the bounded adapter grammar")
    return left, right


def _positive_integer(value: str) -> int | None:
    if not value.strip():
        return None
    return _required_integer(value.strip())


def _required_integer(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError("positive integer expected")
    parsed = int(value)
    if parsed < 1:
        raise ValueError("positive integer expected")
    return parsed


def _fail_fast(value: FailFastChoice) -> bool | None:
    if value is FailFastChoice.INHERIT:
        return None
    return value is FailFastChoice.ENABLED


def _invalid(fallback: CompareDraft, code: CompareDraftValidationCode) -> CompareDraft:
    return msgspec.structs.replace(fallback, validation_code=code)


__all__ = (
    "FailFastChoice",
    "fail_fast_choice",
    "format_budgets",
    "format_matrices",
    "format_selections",
    "format_value_resources",
    "parse_compare_draft",
    "validation_diagnostics",
)
