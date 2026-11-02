"""Tests for Matrix attachment and planned-cell contracts."""

from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.planning import CellRecipe
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.planning import MatrixAxisPlan
from comparo.core.domain.planning import MatrixCaseCoordinate
from comparo.core.domain.planning import MatrixSpace
from comparo.core.domain.planning import PlannedCell
from comparo.core.domain.primitives import PositiveCount
from tests.core.domain.planning_factories import _definition
from tests.core.domain.planning_factories import _matrix_space


def test_environment_pair_is_directed_typed_and_distinct() -> None:
    baseline = _definition(ResourceKind.ENVIRONMENT, "staging")
    candidate = _definition(ResourceKind.ENVIRONMENT, "prod", digest="b")
    assert EnvironmentPair(baseline=baseline, candidate=candidate).baseline == baseline
    with pytest.raises(ValueError, match="distinct Environments"):
        EnvironmentPair(baseline=baseline, candidate=baseline)
    with pytest.raises(ValueError, match="must identify Environment"):
        EnvironmentPair(
            baseline=_definition(ResourceKind.REQUEST, "health"),
            candidate=candidate,
        )
    with pytest.raises(TypeError, match="DefinitionRef"):
        EnvironmentPair(
            baseline=baseline,
            candidate=cast("DefinitionRef", object()),
        )


def test_matrix_space_is_a_compact_exact_product() -> None:
    assert _matrix_space().cell_count.value == 2
    assert MatrixSpace(axes=(), cell_count=PositiveCount(value=1)).axes == ()
    with pytest.raises(ValueError, match="exact axis product"):
        MatrixSpace(axes=_matrix_space().axes, cell_count=PositiveCount(value=3))
    with pytest.raises(ValueError, match="unique attachment bindings"):
        MatrixSpace(
            axes=(_matrix_space().axes[0], _matrix_space().axes[0]),
            cell_count=PositiveCount(value=4),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        MatrixSpace(
            axes=cast("tuple[MatrixAxisPlan, ...]", list(_matrix_space().axes)),
            cell_count=PositiveCount(value=2),
        )
    with pytest.raises(TypeError, match="MatrixAxisPlan"):
        MatrixSpace(
            axes=cast("tuple[MatrixAxisPlan, ...]", (object(),)),
            cell_count=PositiveCount(value=1),
        )
    with pytest.raises(TypeError, match="PositiveCount"):
        MatrixSpace(
            axes=(),
            cell_count=cast("PositiveCount", object()),
        )


def test_matrix_axis_rejects_wrong_definition_empty_or_duplicate_cases() -> None:
    with pytest.raises(TypeError, match="MatrixBindingId"):
        MatrixAxisPlan(
            binding=cast("MatrixBindingId", object()),
            matrix=_definition(ResourceKind.MATRIX, "cases"),
            cases=(MatrixCaseId(value="one"),),
        )
    with pytest.raises(TypeError, match="DefinitionRef"):
        MatrixAxisPlan(
            binding=MatrixBindingId(value="cases"),
            matrix=cast("DefinitionRef", object()),
            cases=(MatrixCaseId(value="one"),),
        )
    with pytest.raises(ValueError, match="identify Matrix"):
        MatrixAxisPlan(
            binding=MatrixBindingId(value="cases"),
            matrix=_definition(ResourceKind.VALUE, "cases"),
            cases=(MatrixCaseId(value="one"),),
        )
    with pytest.raises(ValueError, match="at least one case"):
        MatrixAxisPlan(
            binding=MatrixBindingId(value="cases"),
            matrix=_definition(ResourceKind.MATRIX, "cases"),
            cases=(),
        )
    case = MatrixCaseId(value="one")
    with pytest.raises(ValueError, match="unique"):
        MatrixAxisPlan(
            binding=MatrixBindingId(value="cases"),
            matrix=_definition(ResourceKind.MATRIX, "cases"),
            cases=(case, case),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        MatrixAxisPlan(
            binding=MatrixBindingId(value="cases"),
            matrix=_definition(ResourceKind.MATRIX, "cases"),
            cases=cast("tuple[MatrixCaseId, ...]", [case]),
        )
    with pytest.raises(TypeError, match="MatrixCaseId"):
        MatrixAxisPlan(
            binding=MatrixBindingId(value="cases"),
            matrix=_definition(ResourceKind.MATRIX, "cases"),
            cases=cast("tuple[MatrixCaseId, ...]", (object(),)),
        )


def test_cell_recipe_accepts_only_request_or_scenario() -> None:
    assert (
        CellRecipe(
            subject=_definition(ResourceKind.SCENARIO, "checkout"),
            matrix=MatrixSpace(axes=(), cell_count=PositiveCount(value=1)),
        ).subject.identity.kind
        is ResourceKind.SCENARIO
    )
    with pytest.raises(ValueError, match="Request or Scenario"):
        CellRecipe(subject=_definition(ResourceKind.VALUE, "payload"), matrix=_matrix_space())
    with pytest.raises(TypeError, match="DefinitionRef"):
        CellRecipe(subject=cast("DefinitionRef", object()), matrix=_matrix_space())
    with pytest.raises(TypeError, match="MatrixSpace"):
        CellRecipe(
            subject=_definition(ResourceKind.REQUEST, "health"),
            matrix=cast("MatrixSpace", object()),
        )


def test_planned_cell_requires_typed_immutable_coordinates() -> None:
    definition = _definition(ResourceKind.REQUEST, "health")
    cell_id = CellId(value=UUID(int=42))
    recipe = CellRecipe(subject=definition, matrix=_matrix_space())
    axis = recipe.matrix.axes[0]
    case = MatrixCaseCoordinate(
        binding=axis.binding,
        matrix=axis.matrix,
        case_id=MatrixCaseId(value="en"),
    )
    assert (
        PlannedCell(
            cell_id=cell_id,
            recipe=recipe,
            matrix_cases=(case,),
        ).subject
        == definition
    )
    with pytest.raises(TypeError, match="CellId"):
        PlannedCell(
            cell_id=cast("CellId", object()),
            recipe=recipe,
            matrix_cases=(case,),
        )
    with pytest.raises(TypeError, match="CellRecipe"):
        PlannedCell(
            cell_id=cell_id,
            recipe=cast("CellRecipe", object()),
            matrix_cases=(case,),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        PlannedCell(
            cell_id=cell_id,
            recipe=recipe,
            matrix_cases=cast("tuple[MatrixCaseCoordinate, ...]", [case]),
        )
    with pytest.raises(TypeError, match="MatrixCaseCoordinate"):
        PlannedCell(
            cell_id=cell_id,
            recipe=recipe,
            matrix_cases=cast("tuple[MatrixCaseCoordinate, ...]", (object(),)),
        )
    undeclared = MatrixCaseCoordinate(
        binding=axis.binding,
        matrix=axis.matrix,
        case_id=MatrixCaseId(value="fr"),
    )
    with pytest.raises(ValueError, match="exactly one case"):
        PlannedCell(
            cell_id=cell_id,
            recipe=recipe,
            matrix_cases=(case, undeclared),
        )


def test_matrix_case_coordinate_qualifies_reused_case_ids() -> None:
    case_id = MatrixCaseId(value="small")
    first = MatrixCaseCoordinate(
        binding=MatrixBindingId(value="payload"),
        matrix=_definition(ResourceKind.MATRIX, "payloads", digest="a"),
        case_id=case_id,
    )
    second = MatrixCaseCoordinate(
        binding=MatrixBindingId(value="locale"),
        matrix=_definition(ResourceKind.MATRIX, "locales", digest="b"),
        case_id=case_id,
    )
    recipe = CellRecipe(
        subject=_definition(ResourceKind.REQUEST, "health"),
        matrix=MatrixSpace(
            axes=(
                MatrixAxisPlan(
                    binding=first.binding,
                    matrix=first.matrix,
                    cases=(case_id,),
                ),
                MatrixAxisPlan(
                    binding=second.binding,
                    matrix=second.matrix,
                    cases=(case_id,),
                ),
            ),
            cell_count=PositiveCount(value=1),
        ),
    )
    cell = PlannedCell(
        cell_id=CellId(value=UUID(int=43)),
        recipe=recipe,
        matrix_cases=(first, second),
    )
    assert cell.matrix_cases == (first, second)

    with pytest.raises(TypeError, match="DefinitionRef"):
        MatrixCaseCoordinate(
            binding=first.binding,
            matrix=cast("DefinitionRef", object()),
            case_id=case_id,
        )
    with pytest.raises(TypeError, match="MatrixBindingId"):
        MatrixCaseCoordinate(
            binding=cast("MatrixBindingId", object()),
            matrix=first.matrix,
            case_id=case_id,
        )
    with pytest.raises(ValueError, match="identify Matrix"):
        MatrixCaseCoordinate(
            binding=first.binding,
            matrix=_definition(ResourceKind.REQUEST, "wrong"),
            case_id=case_id,
        )
    with pytest.raises(TypeError, match="MatrixCaseId"):
        MatrixCaseCoordinate(
            binding=first.binding,
            matrix=first.matrix,
            case_id=cast("MatrixCaseId", object()),
        )


def test_matrix_bindings_allow_repeated_definitions_and_cells_require_exact_recipe() -> None:
    matrix = _definition(ResourceKind.MATRIX, "shared")
    first = MatrixAxisPlan(
        binding=MatrixBindingId(value="left"),
        matrix=matrix,
        cases=(MatrixCaseId(value="one"), MatrixCaseId(value="two")),
    )
    second = MatrixAxisPlan(
        binding=MatrixBindingId(value="right"),
        matrix=matrix,
        cases=(MatrixCaseId(value="one"), MatrixCaseId(value="two")),
    )
    recipe = CellRecipe(
        subject=_definition(ResourceKind.REQUEST, "health"),
        matrix=MatrixSpace(axes=(first, second), cell_count=PositiveCount(value=4)),
    )
    exact = (
        MatrixCaseCoordinate(binding=first.binding, matrix=matrix, case_id=first.cases[0]),
        MatrixCaseCoordinate(binding=second.binding, matrix=matrix, case_id=second.cases[1]),
    )
    assert PlannedCell(cell_id=CellId(value=UUID(int=44)), recipe=recipe, matrix_cases=exact)
    with pytest.raises(ValueError, match="ordered axes"):
        PlannedCell(
            cell_id=CellId(value=UUID(int=45)),
            recipe=recipe,
            matrix_cases=tuple(reversed(exact)),
        )
    foreign = MatrixCaseCoordinate(
        binding=first.binding,
        matrix=_definition(ResourceKind.MATRIX, "foreign"),
        case_id=first.cases[0],
    )
    with pytest.raises(ValueError, match="ordered axes"):
        PlannedCell(
            cell_id=CellId(value=UUID(int=46)),
            recipe=recipe,
            matrix_cases=(foreign, exact[1]),
        )
    undeclared = MatrixCaseCoordinate(
        binding=first.binding,
        matrix=matrix,
        case_id=MatrixCaseId(value="three"),
    )
    with pytest.raises(ValueError, match="declared"):
        PlannedCell(
            cell_id=CellId(value=UUID(int=47)),
            recipe=recipe,
            matrix_cases=(undeclared, exact[1]),
        )
