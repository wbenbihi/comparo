"""Scope-lattice projection and immutable binding-snapshot behavior."""

from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.planning import PlannedCell
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.planning import iter_plan_cells
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.models import CellInputs
from comparo.core.resolution.models import MatrixInputBinding
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.scopes import cell_scope
from comparo.core.resolution.scopes import concrete_scope
from comparo.core.resolution.scopes import environment_scope
from comparo.core.resolution.scopes import execution_scope
from comparo.core.resolution.scopes import trial_scope
from comparo.core.resolution.snapshots import NamespaceBinding
from comparo.core.resolution.snapshots import cell_inputs
from comparo.core.resolution.snapshots import environment_snapshot
from comparo.core.resolution.snapshots import matrix_binding
from comparo.core.resolution.snapshots import namespace_binding
from comparo.core.resolution.snapshots import prepared_run_scope
from comparo.core.resolution.snapshots import validate_environment_snapshots
from tests.core.resolution.conftest import configuration_provenance
from tests.core.resolution.conftest import execution_scope as fixture_execution_scope
from tests.core.resolution.conftest import matrix_inputs
from tests.core.resolution.conftest import snapshots


def _coordinates(
    prepared: PreparedRun,
) -> tuple[
    RunScope,
    CellScope,
    TrialScope,
    EnvironmentScope,
    ExecutionScope,
    StepScope,
    AttemptScope,
    AttemptScope,
]:
    execution = fixture_execution_scope(prepared, 0)
    run = execution.trial.cell.run
    cell = execution.trial.cell
    trial = execution.trial
    environment = execution.environment
    step_id = StepExecutionId(value=UUID(int=61))
    step = StepScope(execution=execution, step_execution_id=step_id)
    attempt = AttemptScope(
        execution=execution,
        attempt_id=AttemptId(value=UUID(int=62)),
    )
    step_attempt = msgspec.structs.replace(attempt, step_execution_id=step_id)
    return run, cell, trial, environment, execution, step, attempt, step_attempt


def test_scope_projection_respects_the_non_linear_cell_environment_lattice(
    prepared_run: PreparedRun,
) -> None:
    run, cell, trial, environment, execution, step, attempt, step_attempt = _coordinates(
        prepared_run
    )
    assert cell_scope(cell) is cell
    assert cell_scope(trial) is cell
    assert cell_scope(execution) is cell
    assert cell_scope(run) is None
    assert trial_scope(trial) is trial
    assert trial_scope(step) is trial
    assert trial_scope(environment) is None
    assert environment_scope(environment) is environment
    assert environment_scope(attempt) is environment
    assert environment_scope(cell) is None
    assert execution_scope(execution) is execution
    assert execution_scope(step) is execution
    assert execution_scope(attempt) is execution
    assert execution_scope(run) is None

    assert concrete_scope(ValueScopeKind.RUN, attempt) is run
    assert concrete_scope(ValueScopeKind.CELL, attempt) is cell
    assert concrete_scope(ValueScopeKind.TRIAL, attempt) is trial
    assert concrete_scope(ValueScopeKind.ENVIRONMENT, attempt) is environment
    assert concrete_scope(ValueScopeKind.EXECUTION, attempt) is execution
    assert concrete_scope(ValueScopeKind.STEP, step) is step
    assert concrete_scope(ValueScopeKind.STEP, step_attempt) == step
    assert concrete_scope(ValueScopeKind.STEP, attempt) is None
    assert concrete_scope(ValueScopeKind.ATTEMPT, attempt) is attempt
    assert concrete_scope(ValueScopeKind.ATTEMPT, execution) is None
    with pytest.raises(TypeError, match="ValueScopeKind"):
        concrete_scope(cast("ValueScopeKind", object()), attempt)


def test_target_cell_identity_accepts_every_cell_descendant(prepared_run: PreparedRun) -> None:
    _, cell, trial, _, execution, step, attempt, _ = _coordinates(prepared_run)
    inputs = matrix_inputs(prepared_run)
    for scope in (cell, trial, execution, step, attempt):
        assert CellInputs(cell_id=inputs.cell_id, bindings=inputs.bindings)
        assert ResolutionTarget(
            symbol=SymbolPath(segments=("value",)),
            scope=scope,
            cell_inputs=inputs,
        )


def test_cell_input_snapshot_rejects_plan_drift(prepared_run: PreparedRun) -> None:
    planned = next(iter(iter_plan_cells(prepared_run.plan)))
    assert cell_inputs(prepared_run, planned) == matrix_inputs(prepared_run)
    with pytest.raises(TypeError, match="PreparedRun"):
        cell_inputs(cast("PreparedRun", object()), planned)
    with pytest.raises(TypeError, match="PlannedCell"):
        cell_inputs(prepared_run, cast("PlannedCell", object()))

    absent_identity = msgspec.structs.replace(
        planned.subject.identity,
        resource_id=ResourceId(value="absent"),
    )
    absent_subject = msgspec.structs.replace(
        planned,
        recipe=msgspec.structs.replace(
            planned.recipe,
            subject=msgspec.structs.replace(planned.subject, identity=absent_identity),
        ),
    )
    with pytest.raises(ValueError, match="subject is absent"):
        cell_inputs(prepared_run, absent_subject)

    subject = prepared_run.effective.subjects[0]
    no_matrix = msgspec.structs.replace(
        prepared_run,
        effective=msgspec.structs.replace(
            prepared_run.effective,
            subjects=(msgspec.structs.replace(subject, matrices=()),),
        ),
    )
    with pytest.raises(ValueError, match="Matrix coordinate"):
        cell_inputs(no_matrix, planned)

    matrix = subject.matrices[0]
    case = matrix.cases[0]
    absent_case = msgspec.structs.replace(
        case,
        case_id=msgspec.structs.replace(case.case_id, value="absent"),
    )
    bad_cases = msgspec.structs.replace(
        prepared_run,
        effective=msgspec.structs.replace(
            prepared_run.effective,
            subjects=(
                msgspec.structs.replace(
                    subject,
                    matrices=(msgspec.structs.replace(matrix, cases=(absent_case,)),),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="no object"):
        cell_inputs(bad_cases, planned)

    scalar_case = msgspec.structs.replace(
        case,
        values=LiteralExpr(value=JsonString(value="scalar"), provenance=configuration_provenance()),
    )
    scalar_cases = msgspec.structs.replace(
        prepared_run,
        effective=msgspec.structs.replace(
            prepared_run.effective,
            subjects=(
                msgspec.structs.replace(
                    subject,
                    matrices=(msgspec.structs.replace(matrix, cases=(scalar_case,)),),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="no object"):
        cell_inputs(scalar_cases, planned)


def test_environment_snapshots_require_exact_selected_revision_coverage(
    prepared_run: PreparedRun,
) -> None:
    selected = snapshots(prepared_run)
    validate_environment_snapshots(prepared_run, selected)
    with pytest.raises(TypeError, match="typed immutable tuple"):
        validate_environment_snapshots(prepared_run, cast("Any", list(selected)))
    with pytest.raises(TypeError, match="typed immutable tuple"):
        validate_environment_snapshots(prepared_run, cast("Any", (object(),)))
    with pytest.raises(ValueError, match="unique"):
        validate_environment_snapshots(prepared_run, (selected[0], selected[0]))
    with pytest.raises(ValueError, match="exactly cover"):
        validate_environment_snapshots(prepared_run, (selected[0],))
    assert environment_snapshot(selected, fixture_execution_scope(prepared_run, 0).environment)
    missing_environment = msgspec.structs.replace(
        fixture_execution_scope(prepared_run, 0).environment,
        environment=msgspec.structs.replace(
            fixture_execution_scope(prepared_run, 0).environment.environment,
            resource_id=ResourceId(value="missing"),
        ),
    )
    with pytest.raises(ValueError, match="no run-start snapshot"):
        environment_snapshot(selected, missing_environment)


def test_namespace_binding_applies_precedence_and_scope(prepared_run: PreparedRun) -> None:
    target = fixture_execution_scope(prepared_run, 0)
    greeting = namespace_binding(
        prepared_run,
        ValueNamespace.VARIABLES,
        SymbolPath(segments=("greeting",)),
        target,
    )
    region = namespace_binding(
        prepared_run,
        ValueNamespace.VARIABLES,
        SymbolPath(segments=("region",)),
        target,
    )
    secret = namespace_binding(
        prepared_run,
        ValueNamespace.SECRETS,
        SymbolPath(segments=("token",)),
        target,
    )
    assert greeting is not None
    assert type(greeting.production_scope) is RunScope
    assert region is not None
    assert region.production_scope == target.environment
    assert secret is not None
    assert (
        namespace_binding(
            prepared_run,
            ValueNamespace.VARIABLES,
            SymbolPath(segments=("missing",)),
            target,
        )
        is None
    )
    scalar_layer = msgspec.structs.replace(
        prepared_run.effective.value_layers[0],
        variables=LiteralExpr(
            value=JsonString(value="scalar"),
            provenance=configuration_provenance(),
        ),
    )
    scalar_prepared = msgspec.structs.replace(
        prepared_run,
        effective=msgspec.structs.replace(
            prepared_run.effective,
            value_layers=(scalar_layer,),
        ),
    )
    assert (
        namespace_binding(
            scalar_prepared,
            ValueNamespace.VARIABLES,
            SymbolPath(segments=("missing",)),
            target,
        )
        is None
    )
    assert (
        namespace_binding(
            prepared_run,
            ValueNamespace.VARIABLES,
            SymbolPath(segments=("region",)),
            target.trial.cell,
        )
        is None
    )
    with pytest.raises(ValueError, match="variables, secrets, and parameters"):
        namespace_binding(prepared_run, ValueNamespace.ENV, SymbolPath(segments=("X",)), target)

    parameters = ObjectExpr(
        members=(
            ObjectExpressionMember(
                name="count",
                expression=LiteralExpr(
                    value=JsonInteger(value=2),
                    provenance=configuration_provenance(),
                ),
            ),
        ),
        provenance=configuration_provenance(),
    )
    base_layer = prepared_run.effective.value_layers[0]
    global_parameter = msgspec.structs.replace(
        base_layer,
        variables=None,
        parameters=parameters,
    )
    environment_parameter = msgspec.structs.replace(
        global_parameter,
        environment=prepared_run.effective.environments[0],
    )
    layered = msgspec.structs.replace(
        prepared_run,
        effective=msgspec.structs.replace(
            prepared_run.effective,
            value_layers=(global_parameter, environment_parameter),
        ),
    )
    parameter = namespace_binding(
        layered,
        ValueNamespace.PARAMETERS,
        SymbolPath(segments=("count",)),
        target,
    )
    assert parameter is not None
    assert parameter.production_scope is target
    only_global = msgspec.structs.replace(
        layered,
        effective=msgspec.structs.replace(layered.effective, value_layers=(global_parameter,)),
    )
    parameter = namespace_binding(
        only_global,
        ValueNamespace.PARAMETERS,
        SymbolPath(segments=("count",)),
        target,
    )
    assert parameter is not None
    assert parameter.production_scope == target.trial.cell
    assert prepared_run_scope(target) == target.trial.cell.run


def test_namespace_binding_model_and_matrix_lookup_are_unambiguous(
    prepared_run: PreparedRun,
) -> None:
    expression = LiteralExpr(
        value=JsonString(value="value"),
        provenance=configuration_provenance(),
    )
    binding = NamespaceBinding(
        expression=expression,
        production_scope=fixture_execution_scope(prepared_run, 0),
        configuration=configuration_provenance(),
    )
    with pytest.raises(TypeError, match="ValueExpression"):
        msgspec.structs.replace(binding, expression=cast("Any", object()))
    with pytest.raises(TypeError, match="ValueProvenance"):
        msgspec.structs.replace(binding, configuration=cast("Any", object()))

    inputs = matrix_inputs(prepared_run)
    locale = SymbolPath(segments=("locale",))
    assert matrix_binding(inputs, locale) is not None
    assert matrix_binding(inputs, SymbolPath(segments=("cases", "locale"))) is not None
    assert matrix_binding(inputs, SymbolPath(segments=("missing", "locale"))) is None
    assert matrix_binding(inputs, SymbolPath(segments=("cases", "missing"))) is None
    assert matrix_binding(None, locale) is None

    duplicate = CellInputs(
        cell_id=inputs.cell_id,
        bindings=(
            inputs.bindings[0],
            MatrixInputBinding(
                binding=msgspec.structs.replace(inputs.bindings[0].binding, value="other"),
                case_id=inputs.bindings[0].case_id,
                values=inputs.bindings[0].values,
            ),
        ),
    )
    assert matrix_binding(duplicate, locale) is None
    nested = ObjectExpr(
        members=(ObjectExpressionMember(name="scalar", expression=expression),),
        provenance=configuration_provenance(),
    )
    nested_inputs = CellInputs(
        cell_id=inputs.cell_id,
        bindings=(
            msgspec.structs.replace(
                inputs.bindings[0],
                values=ObjectExpr(
                    members=(ObjectExpressionMember(name="nested", expression=nested),),
                    provenance=configuration_provenance(),
                ),
            ),
        ),
    )
    assert (
        matrix_binding(
            nested_inputs,
            SymbolPath(segments=("cases", "nested", "scalar")),
        )
        is not None
    )
    assert (
        matrix_binding(
            nested_inputs,
            SymbolPath(segments=("cases", "nested", "scalar", "extra")),
        )
        is None
    )
