"""Pure fail-closed orchestration from CompiledProject to immutable RunPlan."""

from __future__ import annotations

from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.planning import CellRecipe
from comparo.core.domain.planning import MatrixAxisPlan
from comparo.core.domain.planning import MatrixSpace
from comparo.core.domain.planning import PlanOrdering
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning import PlanSliceCardinality
from comparo.core.domain.planning import RunPlan
from comparo.core.domain.planning import RunPlanCardinality
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.planning._errors import PlanningError
from comparo.core.planning.binding import bind_execution
from comparo.core.planning.budgeting import admit_cardinality
from comparo.core.planning.budgeting import admit_request_count
from comparo.core.planning.fingerprints import plan_fingerprint
from comparo.core.planning.models import EffectiveExecution
from comparo.core.planning.models import PlanningResult
from comparo.core.planning.models import PreparedRun


def plan_run(
    project: CompiledProject,
    invocation: RunInvocation,
    authority: PlanningAuthority,
) -> PlanningResult:
    """Bind and admit one compact plan without I/O, secret resolution, clocks, or randomness."""
    if type(project) is not CompiledProject:
        raise TypeError("run planning requires a CompiledProject")
    if type(invocation) is not RunInvocation:
        raise TypeError("run planning requires a RunInvocation")
    if type(authority) is not PlanningAuthority:
        raise TypeError("run planning requires PlanningAuthority")
    try:
        effective, budgets = bind_execution(project, invocation, authority)
        slices = _plan_slices(effective)
        cardinality = _total_cardinality(slices)
        admit_request_count(budgets, len(slices))
        admit_cardinality(budgets, cardinality)
        fingerprint = plan_fingerprint(effective, invocation, authority, slices, budgets)
        plan = RunPlan(
            run_id=invocation.run_id,
            slices=slices,
            budgets=budgets,
            ordering=PlanOrdering.SUBJECT_MATRIX_TRIAL_ENVIRONMENT,
            cardinality=cardinality,
            fingerprint=fingerprint,
        )
        return PlanningResult(
            prepared=PreparedRun(
                project=project,
                invocation=invocation,
                authority=authority,
                effective=effective,
                plan=plan,
            )
        )
    except PlanningError as error:
        return PlanningResult(diagnostics=(error.diagnostic(),))


def _plan_slices(effective: EffectiveExecution) -> tuple[PlanSlice, ...]:
    return tuple(
        _plan_slice(effective, subject_index) for subject_index in range(len(effective.subjects))
    )


def _plan_slice(effective: EffectiveExecution, subject_index: int) -> PlanSlice:
    subject = effective.subjects[subject_index]
    axes = tuple(
        MatrixAxisPlan(
            binding=matrix.binding,
            matrix=matrix.definition,
            cases=tuple(case.case_id for case in matrix.cases),
        )
        for matrix in subject.matrices
    )
    cells = 1
    for axis in axes:
        cells *= len(axis.cases)
    matrix = MatrixSpace(axes=axes, cell_count=PositiveCount(value=cells))
    trials = cells * effective.trials.value
    executions = trials * len(effective.environments)
    cardinality = PlanSliceCardinality(
        cells=Count(value=cells),
        trials=Count(value=trials),
        environment_executions=Count(value=executions),
        comparisons=Count(value=trials * len(effective.comparisons)),
        maximum_attempts=Count(value=executions * effective.maximum_attempts.value),
    )
    return PlanSlice(
        cells=CellRecipe(subject=subject.definition, matrix=matrix),
        trial_count=effective.trials,
        environments=effective.environments,
        comparisons=effective.comparisons,
        maximum_attempts_per_execution=effective.maximum_attempts,
        cardinality=cardinality,
    )


def _total_cardinality(slices: tuple[PlanSlice, ...]) -> RunPlanCardinality:
    return RunPlanCardinality(
        cells=Count(value=sum(item.cardinality.cells.value for item in slices)),
        trials=Count(value=sum(item.cardinality.trials.value for item in slices)),
        environment_executions=Count(
            value=sum(item.cardinality.environment_executions.value for item in slices)
        ),
        comparisons=Count(value=sum(item.cardinality.comparisons.value for item in slices)),
        maximum_attempts=Count(
            value=sum(item.cardinality.maximum_attempts.value for item in slices)
        ),
    )
