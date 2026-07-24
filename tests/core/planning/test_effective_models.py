"""Direct-construction invariants for effective and admitted planning products."""

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import Count
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.planning import iter_plan_cells
from comparo.core.planning import plan_run
from comparo.core.planning.models import EffectiveFieldProvenance
from comparo.core.planning.models import EffectiveProfiles
from comparo.core.planning.models import PlannedTrial
from comparo.core.planning.models import PlanningResult
from comparo.core.planning.models import PlanningSource
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.models import is_definition_handle
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import invocation


def _prepared(project: CompiledProject) -> PreparedRun:
    result = plan_run(project, invocation(), authority())
    assert result.prepared is not None
    return result.prepared


def test_planning_sources_and_field_provenance_are_exact(
    comparison_project: CompiledProject,
) -> None:
    prepared = _prepared(comparison_project)
    definition = prepared.effective.subjects[0].definition
    invocation_id = prepared.invocation.invocation_id
    assert is_definition_handle(definition)
    assert not is_definition_handle(object())
    definition_source = PlanningSource(kind=ProvenanceKind.PROJECT, definition=definition)
    invocation_source = PlanningSource(
        kind=ProvenanceKind.INVOCATION,
        invocation_id=invocation_id,
    )
    assert PlanningSource(kind=ProvenanceKind.VERSIONED_DEFAULT).definition is None
    source_cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"kind": "project"}, "ProvenanceKind"),
        ({"definition": object()}, "definition handle"),
        ({"invocation_id": object()}, "InvocationId"),
        ({"kind": ProvenanceKind.INVOCATION, "definition": definition}, "matching"),
        ({"kind": ProvenanceKind.PROJECT, "definition": None}, "matching"),
        (
            {
                "kind": ProvenanceKind.VERSIONED_DEFAULT,
                "invocation_id": invocation_id,
            },
            "matching",
        ),
    )
    for changes, message in source_cases:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(definition_source, **changes)
    provenance = EffectiveFieldProvenance(
        field="subjects",
        sources=(definition_source, invocation_source),
    )
    provenance_cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"field": ""}, "field name"),
        ({"field": "x" * 65}, "field name"),
        ({"sources": []}, "immutable"),
        ({"sources": (object(),)}, "immutable"),
        ({"sources": ()}, "must not be empty"),
    )
    for changes, message in provenance_cases:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(provenance, **changes)


def test_matrix_and_subject_effective_models_validate_closed_structure(
    comparison_project: CompiledProject,
) -> None:
    prepared = _prepared(comparison_project)
    subject = prepared.effective.subjects[0]
    matrix = subject.matrices[0]
    case = matrix.cases[0]
    environment = prepared.effective.environments[0]
    case_cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"case_id": object()}, "MatrixCaseId"),
        ({"values": object()}, "ValueExpression"),
    )
    for changes, message in case_cases:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(case, **changes)
    matrix_cases: tuple[tuple[dict[str, object], type[Exception], str], ...] = (
        ({"binding": object()}, TypeError, "MatrixBindingId"),
        ({"definition": object()}, TypeError, "DefinitionRef"),
        ({"definition": environment}, ValueError, "identify Matrix"),
        ({"cases": []}, TypeError, "typed immutable"),
        ({"cases": (object(),)}, TypeError, "typed immutable"),
        ({"cases": ()}, ValueError, "at least one"),
        ({"cases": (case, case)}, ValueError, "unique"),
    )
    for changes, error, message in matrix_cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(matrix, **changes)
    subject_cases: tuple[tuple[dict[str, object], type[Exception], str], ...] = (
        ({"definition": object()}, TypeError, "DefinitionRef"),
        ({"definition": environment}, ValueError, "Request or Scenario"),
        ({"matrices": []}, TypeError, "typed immutable"),
        ({"matrices": (object(),)}, TypeError, "typed immutable"),
        ({"matrices": (matrix, matrix)}, ValueError, "unique"),
        ({"profiles": object()}, TypeError, "EffectiveProfiles"),
    )
    for changes, error, message in subject_cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(subject, **changes)


def test_effective_profiles_accept_inline_handles_and_reject_kind_confusion(
    comparison_project: CompiledProject,
) -> None:
    prepared = _prepared(comparison_project)
    subject = prepared.effective.subjects[0]
    request = subject.definition
    environment = prepared.effective.environments[0]
    inline_safety = InlineDefinitionRef(
        kind=ResourceKind.SAFETY_PROFILE,
        origin=InlineDefinitionOrigin(
            owner=request.identity,
            fragment=InlineFragment(value="#safety"),
        ),
        fingerprint=request.fingerprint,
    )
    assert EffectiveProfiles(safety=(inline_safety,)).safety == (inline_safety,)
    with pytest.raises(TypeError, match="exact definition"):
        EffectiveProfiles(assertions=cast("tuple[DefinitionRef, ...]", [request]))
    with pytest.raises(ValueError, match="wrong resource kind"):
        EffectiveProfiles(assertions=(environment,))
    assertion = subject.profiles.assertions[0]
    with pytest.raises(ValueError, match="unique"):
        EffectiveProfiles(assertions=(assertion, assertion))
    with pytest.raises(TypeError, match="exact definition"):
        EffectiveProfiles(gate=cast("DefinitionRef", object()))
    with pytest.raises(ValueError, match="wrong resource kind"):
        EffectiveProfiles(gate=environment)


def test_value_layers_and_effective_execution_validate_topology_and_values(
    comparison_project: CompiledProject,
) -> None:
    prepared = _prepared(comparison_project)
    effective = prepared.effective
    layer = effective.value_layers[0]
    environment = effective.environments[0]
    layer_cases: tuple[tuple[dict[str, object], type[Exception], str], ...] = (
        ({"source": object()}, TypeError, "PlanningSource"),
        ({"environment": effective.subjects[0].definition}, ValueError, "Environment"),
        ({"variables": object()}, TypeError, "ValueExpression"),
        (
            {"parameters": None, "variables": None, "secrets": None},
            ValueError,
            "at least one",
        ),
    )
    for changes, error, message in layer_cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(layer, **changes)
    assert msgspec.structs.replace(layer, environment=environment).environment == environment
    execution_cases: tuple[tuple[dict[str, object], type[Exception], str], ...] = (
        ({"subjects": []}, TypeError, "typed immutable"),
        ({"subjects": ()}, ValueError, "at least one subject"),
        ({"environments": []}, TypeError, "typed immutable"),
        ({"environments": (effective.subjects[0].definition,)}, TypeError, "typed immutable"),
        ({"environments": ()}, ValueError, "at least one Environment"),
        ({"comparisons": []}, TypeError, "typed immutable"),
        ({"comparisons": (object(),)}, TypeError, "typed immutable"),
        ({"trials": object()}, TypeError, "PositiveCount"),
        ({"maximum_attempts": object()}, TypeError, "PositiveCount"),
        ({"fail_fast": object()}, TypeError, "bool"),
        ({"value_layers": []}, TypeError, "typed immutable"),
        ({"value_layers": (object(),)}, TypeError, "typed immutable"),
        ({"provenance": []}, TypeError, "typed immutable"),
        ({"provenance": (object(),)}, TypeError, "typed immutable"),
    )
    for changes, error, message in execution_cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(effective, **changes)


def test_prepared_trial_and_result_models_reject_cross_run_or_ambiguous_outcomes(
    comparison_project: CompiledProject,
) -> None:
    prepared = _prepared(comparison_project)
    for changes, message in (
        ({"project": object()}, "CompiledProject"),
        ({"invocation": object()}, "RunInvocation"),
        ({"authority": object()}, "PlanningAuthority"),
        ({"effective": object()}, "EffectiveExecution"),
        ({"plan": object()}, "RunPlan"),
    ):
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(prepared, **changes)
    other_invocation = msgspec.structs.replace(
        prepared.invocation,
        run_id=msgspec.structs.replace(prepared.invocation.run_id, value=UUID(int=99)),
    )
    with pytest.raises(ValueError, match="share a RunId"):
        msgspec.structs.replace(prepared, invocation=other_invocation)

    cell = next(iter(iter_plan_cells(prepared.plan)))
    trial = PlannedTrial(
        trial_id=TrialId(value=UUID(int=1)),
        cell=cell,
        ordinal=Count(value=0),
    )
    for changes, message in (
        ({"trial_id": object()}, "TrialId"),
        ({"cell": object()}, "PlannedCell"),
        ({"ordinal": object()}, "Count"),
    ):
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(trial, **changes)

    diagnostic = Diagnostic(
        code=DiagnosticCode(value="planning.test.invalid"),
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.PLANNING,
    )
    success = PlanningResult(prepared=prepared)
    failure = PlanningResult(diagnostics=(diagnostic,))
    assert success.prepared is prepared
    assert failure.diagnostics == (diagnostic,)
    with pytest.raises(TypeError, match="PreparedRun"):
        PlanningResult(prepared=cast("PreparedRun", object()))
    with pytest.raises(TypeError, match="typed immutable"):
        PlanningResult(diagnostics=cast("tuple[Diagnostic, ...]", [diagnostic]))
    with pytest.raises(TypeError, match="typed immutable"):
        PlanningResult(diagnostics=(cast("Diagnostic", object()),))
    with pytest.raises(ValueError, match="exactly"):
        PlanningResult()
    with pytest.raises(ValueError, match="exactly"):
        PlanningResult(prepared=prepared, diagnostics=(diagnostic,))
