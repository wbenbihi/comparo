"""Tests for compact immutable planning contracts."""

from uuid import UUID

import pytest

from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.planning import CellRecipe
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.planning import MatrixAxisPlan
from comparo.core.domain.planning import MatrixSpace
from comparo.core.domain.planning import PlanOrdering
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning import PlanSliceCardinality
from comparo.core.domain.planning import RunPlan
from comparo.core.domain.planning import RunPlanCardinality
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest


def _identity(kind: ResourceKind, value: str) -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=kind,
        resource_id=ResourceId(value=value),
    )


def _definition(kind: ResourceKind, value: str, digest: str = "a") -> DefinitionRef:
    return DefinitionRef(
        identity=_identity(kind, value),
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value=digest * 64),
        ),
    )


def _matrix_space() -> MatrixSpace:
    axis = MatrixAxisPlan(
        matrix=_definition(ResourceKind.MATRIX, "locales"),
        cases=(MatrixCaseId(value="en"), MatrixCaseId(value="fr")),
    )
    return MatrixSpace(axes=(axis,), cell_count=PositiveCount(value=2))


def _slice() -> PlanSlice:
    baseline = _identity(ResourceKind.ENVIRONMENT, "staging")
    candidate = _identity(ResourceKind.ENVIRONMENT, "prod")
    return PlanSlice(
        cells=CellRecipe(
            subject=_definition(ResourceKind.REQUEST, "health", digest="b"),
            matrix=_matrix_space(),
        ),
        trial_count=PositiveCount(value=3),
        environments=(baseline, candidate),
        comparisons=(EnvironmentPair(baseline=baseline, candidate=candidate),),
        maximum_attempts_per_execution=PositiveCount(value=2),
        cardinality=PlanSliceCardinality(
            cells=Count(value=2),
            trials=Count(value=6),
            environment_executions=Count(value=12),
            comparisons=Count(value=6),
            maximum_attempts=Count(value=24),
        ),
    )


def test_environment_pair_is_directed_typed_and_distinct() -> None:
    baseline = _identity(ResourceKind.ENVIRONMENT, "staging")
    candidate = _identity(ResourceKind.ENVIRONMENT, "prod")
    assert EnvironmentPair(baseline=baseline, candidate=candidate).baseline == baseline
    with pytest.raises(ValueError, match="distinct Environments"):
        EnvironmentPair(baseline=baseline, candidate=baseline)
    with pytest.raises(ValueError, match="must identify Environment"):
        EnvironmentPair(
            baseline=_identity(ResourceKind.REQUEST, "health"),
            candidate=candidate,
        )


def test_matrix_space_is_a_compact_exact_product() -> None:
    assert _matrix_space().cell_count.value == 2
    assert MatrixSpace(axes=(), cell_count=PositiveCount(value=1)).axes == ()
    with pytest.raises(ValueError, match="exact axis product"):
        MatrixSpace(axes=_matrix_space().axes, cell_count=PositiveCount(value=3))
    with pytest.raises(ValueError, match="unique resource identities"):
        MatrixSpace(
            axes=(_matrix_space().axes[0], _matrix_space().axes[0]),
            cell_count=PositiveCount(value=4),
        )


def test_matrix_axis_rejects_wrong_definition_empty_or_duplicate_cases() -> None:
    with pytest.raises(ValueError, match="identify Matrix"):
        MatrixAxisPlan(
            matrix=_definition(ResourceKind.VALUE, "cases"),
            cases=(MatrixCaseId(value="one"),),
        )
    with pytest.raises(ValueError, match="at least one case"):
        MatrixAxisPlan(matrix=_definition(ResourceKind.MATRIX, "cases"), cases=())
    case = MatrixCaseId(value="one")
    with pytest.raises(ValueError, match="unique"):
        MatrixAxisPlan(
            matrix=_definition(ResourceKind.MATRIX, "cases"),
            cases=(case, case),
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


def test_plan_slice_recomputes_cardinality_and_validates_topology() -> None:
    assert _slice().cardinality.maximum_attempts.value == 24
    invalid = PlanSliceCardinality(
        cells=Count(value=2),
        trials=Count(value=6),
        environment_executions=Count(value=12),
        comparisons=Count(value=7),
        maximum_attempts=Count(value=24),
    )
    source = _slice()
    with pytest.raises(ValueError, match="does not match"):
        PlanSlice(
            cells=source.cells,
            trial_count=source.trial_count,
            environments=source.environments,
            comparisons=source.comparisons,
            maximum_attempts_per_execution=source.maximum_attempts_per_execution,
            cardinality=invalid,
        )
    outsider = _identity(ResourceKind.ENVIRONMENT, "dev")
    with pytest.raises(ValueError, match="must be selected"):
        PlanSlice(
            cells=source.cells,
            trial_count=source.trial_count,
            environments=source.environments,
            comparisons=(EnvironmentPair(baseline=source.environments[0], candidate=outsider),),
            maximum_attempts_per_execution=source.maximum_attempts_per_execution,
            cardinality=source.cardinality,
        )


def test_run_plan_requires_exact_aggregate_without_eager_tasks() -> None:
    plan_slice = _slice()
    cardinality = RunPlanCardinality(
        cells=Count(value=2),
        trials=Count(value=6),
        environment_executions=Count(value=12),
        comparisons=Count(value=6),
        maximum_attempts=Count(value=24),
    )
    plan = RunPlan(
        run_id=RunId(value=UUID("12345678-1234-5678-1234-567812345678")),
        slices=(plan_slice,),
        budgets=(),
        ordering=PlanOrdering.SUBJECT_MATRIX_TRIAL_ENVIRONMENT,
        cardinality=cardinality,
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="f" * 64),
        ),
    )
    assert plan.cardinality == cardinality
    assert not hasattr(plan, "tasks")
    with pytest.raises(ValueError, match="slice totals"):
        RunPlan(
            run_id=plan.run_id,
            slices=plan.slices,
            budgets=plan.budgets,
            ordering=plan.ordering,
            cardinality=RunPlanCardinality(
                cells=Count(value=3),
                trials=cardinality.trials,
                environment_executions=cardinality.environment_executions,
                comparisons=cardinality.comparisons,
                maximum_attempts=cardinality.maximum_attempts,
            ),
            fingerprint=plan.fingerprint,
        )
