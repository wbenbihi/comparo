"""Matrix grammar failures, expansion bounds, and anonymous safety admission."""

import pytest

from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.primitives import Count
from comparo.core.planning import plan_run
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _matrix_project(spec: str) -> object:
    return compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "matrices: [{$use: cases}]}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Matrix", "cases", spec),
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
    )


def _planning_code(spec: str, *, limit: int = 100_000) -> str:
    result = plan_run(_matrix_project(spec), invocation(), authority(limit))  # type: ignore[arg-type]
    assert result.prepared is None
    return result.diagnostics[0].code.value


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ("{cases: [], combine: [product]}", "planning.matrix.combine-invalid"),
        ("{cases: [], combine: diagonal}", "planning.matrix.combine-unsupported"),
        ("{cases: {id: one}}", "planning.matrix.cases-invalid"),
        ("{cases: [plain]}", "planning.matrix.case-invalid"),
        (
            "{cases: [{id: {$env: CASE_ID}, values: {value: one}}]}",
            "planning.matrix.static-id-required",
        ),
        ("{cases: [{id: Bad, values: {value: one}}]}", "planning.matrix.case-id-invalid"),
        ("{cases: [{id: one, values: plain}]}", "planning.matrix.values-invalid"),
        (
            "{cases: [{id: one, values: {value: 1}}, {id: one, values: {value: 2}}]}",
            "planning.matrix.case-id-duplicate",
        ),
        (
            "{cases: [{id: one, values: {value: 1}}], exclude: [one]}",
            "planning.matrix.no-cases",
        ),
        (
            "{cases: [{id: one, values: {value: 1}}], exclude: plain}",
            "planning.matrix.exclude-invalid",
        ),
        (
            "{cases: [{id: one, values: {value: 1}}], exclude: [Bad]}",
            "planning.matrix.exclude-invalid",
        ),
        (
            "{cases: [{id: one, values: {value: 1}}], exclude: [one, one]}",
            "planning.matrix.exclude-duplicate",
        ),
        ("{axes: {}}", "planning.matrix.axes-invalid"),
        (
            "{axes: {Bad: [{id: one, value: 1}]}}",
            "planning.matrix.axis-id-invalid",
        ),
        ("{axes: {good: plain}}", "planning.matrix.axis-invalid"),
        ("{axes: {good: [plain]}}", "planning.matrix.axis-value-invalid"),
        (
            "{axes: {good: [{id: one}]}}",
            "planning.matrix.axis-value-invalid",
        ),
        (
            "{axes: {good: [{id: one, value: 1}, {id: one, value: 2}]}}",
            "planning.matrix.axis-case-duplicate",
        ),
        (
            "{axes: {left: [{id: one, value: 1}], right: [{id: one, value: 1}, "
            "{id: two, value: 2}]}, combine: zip}",
            "planning.matrix.zip-length",
        ),
    ],
)
def test_invalid_matrix_shapes_fail_with_stable_diagnostics(spec: str, code: str) -> None:
    assert _planning_code(spec) == code


@pytest.mark.parametrize(
    "spec",
    [
        "{cases: [{id: one, values: {value: 1}}, {id: two, values: {value: 2}}]}",
        "{axes: {left: [{id: one, value: 1}, {id: two, value: 2}]}, combine: product}",
        "{axes: {left: [{id: one, value: 1}, {id: two, value: 2}]}, combine: zip}",
        "{axes: {left: [{id: one, value: 1}, {id: two, value: 2}]}, combine: union}",
    ],
)
def test_every_matrix_combination_mode_is_bounded_before_expansion(spec: str) -> None:
    assert _planning_code(spec, limit=1) == "planning.matrix.expansion-exceeded"


def test_long_generated_axis_case_ids_are_stable_and_bounded() -> None:
    first = "a" * 40
    second = "b" * 40
    project = _matrix_project(
        f"{{axes: {{left: [{{id: {first}, value: 1}}], right: [{{id: {second}, value: 2}}]}}}}"
    )
    result = plan_run(project, invocation(), authority())  # type: ignore[arg-type]
    assert result.prepared is not None
    case_id = result.prepared.effective.subjects[0].matrices[0].cases[0].case_id.value
    assert len(case_id) == 63
    assert case_id.startswith("a")


def test_duplicate_unqualified_fields_across_matrices_are_rejected() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "matrices: [{$use: left}, {$use: right}]}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Matrix", "left", "{cases: [{id: one, values: {shared: 1}}]}"),
        document("Matrix", "right", "{cases: [{id: one, values: {shared: 2}}]}"),
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
    )
    result = plan_run(project, invocation(), authority())
    assert result.prepared is None
    assert result.diagnostics[0].code.value == "planning.matrix.field-ambiguous"


def test_inline_safety_has_exact_fingerprinted_ceiling_and_plan_identity() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "safety: {budgets: {matrix-cells: 2}}}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
    )
    result = plan_run(project, invocation(), authority())
    assert result.prepared is not None
    subject = result.prepared.effective.subjects[0]
    assert subject.profiles.safety[0].fingerprint.digest.hex_value
    budget = next(
        item
        for item in result.prepared.plan.budgets
        if item.resource is BudgetResource.MATRIX_CELLS
    )
    assert isinstance(budget.cap, Count)
    assert budget.cap.value == 2
