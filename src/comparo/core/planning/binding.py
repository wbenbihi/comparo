"""Field-owned precedence binding from compiled templates to effective execution."""

from __future__ import annotations

from typing import NamedTuple

import msgspec

from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.immutability import ImmutableDomainValue
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import ClearBase
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import ComparisonTopologyEdit
from comparo.core.domain.planning_inputs import InheritTopology
from comparo.core.domain.planning_inputs import InvocationRequestEdit
from comparo.core.domain.planning_inputs import MatrixSelection
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.planning_profiles import InvocationProfileOverrides
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.values import JsonBoolean
from comparo.core.planning._canonical import find_resource
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import positive_integer
from comparo.core.planning._canonical import resource_spec
from comparo.core.planning._errors import PlanningError as PlanningProblem
from comparo.core.planning.budgeting import compose_planning_budgets
from comparo.core.planning.budgeting import effective_limit
from comparo.core.planning.matrices import compile_matrix
from comparo.core.planning.matrices import validate_unqualified_fields
from comparo.core.planning.models import DefinitionHandle
from comparo.core.planning.models import EffectiveExecution
from comparo.core.planning.models import EffectiveFieldProvenance
from comparo.core.planning.models import EffectiveMatrix
from comparo.core.planning.models import EffectiveProfiles
from comparo.core.planning.models import EffectiveSubject
from comparo.core.planning.models import PlanningSource
from comparo.core.planning.models import ValueBindingLayer

type ProfileResource = CompiledResource | InlineResource


class _Layer(NamedTuple):
    """One ordinary structural-precedence layer."""

    resource: CompiledResource
    spec: CanonicalObject
    source_kind: ProvenanceKind


def bind_execution(
    project: CompiledProject,
    invocation: RunInvocation,
    authority: PlanningAuthority,
) -> tuple[EffectiveExecution, tuple[ResourceBudget, ...]]:
    """Bind structural layers, profiles, values, authority, and planning budgets."""
    project_spec = resource_spec(project.project)
    defaults_value = object_member(project_spec, "defaults")
    if type(defaults_value) is not CanonicalObject:
        raise PlanningProblem(
            "planning.project.defaults-missing",
            resource=project.project,
            field="defaults",
        )
    layers = [_Layer(project.project, defaults_value, ProvenanceKind.PROJECT)]
    execution_profile = _selected_execution_profile(project, invocation)
    if execution_profile is not None:
        layers.append(
            _Layer(
                execution_profile,
                resource_spec(execution_profile),
                ProvenanceKind.PROFILE,
            )
        )

    subject_resources, subject_sources = _bind_subjects(project, tuple(layers), invocation)
    _validate_request_edit_targets(invocation.requests, subject_resources)
    environments, comparisons, topology_sources = _bind_topology(project, tuple(layers), invocation)
    _validate_environment_value_targets(invocation, environments)
    _authorize_selection(subject_resources, environments, authority)
    trial_count, trial_sources = _bind_count(
        tuple(layers), invocation.trials, field="trials", default=1, invocation=invocation
    )
    attempt_count, attempt_sources = _bind_count(
        tuple(layers),
        invocation.maximum_attempts,
        field="maximumAttempts",
        default=1,
        invocation=invocation,
    )
    fail_fast, fail_fast_sources = _bind_bool(
        tuple(layers), invocation.fail_fast, field="failFast", default=False, invocation=invocation
    )
    matrix_selections, matrix_sources = _bind_matrix_selections(project, tuple(layers), invocation)

    profile_pairs = tuple(
        (
            subject,
            _effective_profiles(
                project,
                tuple(layers),
                subject,
                environments,
                invocation.profiles,
            ),
        )
        for subject in subject_resources
    )
    safety_profiles = _all_safety_resources(
        project,
        tuple(layers),
        subject_resources,
        environments,
        invocation.profiles.safety,
    )
    budgets = compose_planning_budgets(
        project_resource=project.project,
        project_defaults=defaults_value,
        execution_profile=execution_profile,
        safety_profiles=safety_profiles,
        invocation=invocation,
        authority=authority,
    )
    matrix_limit = effective_limit(budgets, BudgetResource.MATRIX_CELLS)
    effective_subjects = tuple(
        EffectiveSubject(
            definition=subject.definition,
            matrices=_subject_matrices(
                project,
                subject,
                matrix_selections,
                maximum_cells=matrix_limit,
            ),
            profiles=profiles,
            request_edit=_request_edit(invocation, subject),
        )
        for subject, profiles in profile_pairs
    )
    value_layers = _value_layers(
        project,
        project_spec,
        defaults_value,
        execution_profile,
        environments,
        invocation,
    )
    provenance = (
        EffectiveFieldProvenance(field="subjects", sources=subject_sources),
        EffectiveFieldProvenance(field="topology", sources=topology_sources),
        EffectiveFieldProvenance(field="matrices", sources=matrix_sources),
        EffectiveFieldProvenance(field="trials", sources=trial_sources),
        EffectiveFieldProvenance(field="maximumAttempts", sources=attempt_sources),
        EffectiveFieldProvenance(field="failFast", sources=fail_fast_sources),
        EffectiveFieldProvenance(
            field="safety",
            sources=(
                *tuple(_profile_source(profile) for profile in safety_profiles),
                PlanningSource(
                    kind=ProvenanceKind.EXECUTION_POLICY,
                    definition=authority.policy,
                ),
            ),
        ),
    )
    return (
        EffectiveExecution(
            subjects=effective_subjects,
            environments=tuple(resource.definition for resource in environments),
            comparisons=comparisons,
            trials=trial_count,
            maximum_attempts=attempt_count,
            fail_fast=fail_fast,
            value_layers=value_layers,
            provenance=provenance,
        ),
        budgets,
    )


def _bind_bool(
    layers: tuple[_Layer, ...],
    override: Inherit | Clear | SetValue[bool],
    *,
    field: str,
    default: bool,
    invocation: RunInvocation,
) -> tuple[bool, tuple[PlanningSource, ...]]:
    """Bind one static boolean with the same explicit scalar precedence as counts."""
    value = default
    sources = [PlanningSource(kind=ProvenanceKind.VERSIONED_DEFAULT)]
    for layer in layers:
        candidate = object_member(layer.spec, field)
        if candidate is not None:
            if type(candidate) is not JsonBoolean:
                raise PlanningProblem(
                    "planning.field.shape-invalid", resource=layer.resource, field=field
                )
            value = candidate.value
            sources.append(_layer_source(layer))
    if type(override) is Clear:
        value = default
        sources.append(_invocation_source(invocation))
    elif type(override) is SetValue:
        value = override.value
        sources.append(_invocation_source(invocation))
    elif type(override) is not Inherit:
        raise TypeError("planner boolean override must be typed")
    return value, tuple(sources)


def _selected_execution_profile(
    project: CompiledProject,
    invocation: RunInvocation,
) -> CompiledResource | None:
    if invocation.execution_profile is None:
        return None
    return find_resource(
        project,
        ResourceKind.EXECUTION_PROFILE,
        invocation.execution_profile,
        owner=project.project,
        field="executionProfile",
    )


def _bind_subjects(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    invocation: RunInvocation,
) -> tuple[tuple[CompiledResource, ...], tuple[PlanningSource, ...]]:
    selected: tuple[ResourceId, ...] = ()
    sources: list[PlanningSource] = []
    for layer in layers:
        value = object_member(layer.spec, "subjects")
        if value is not None:
            selected = tuple(
                resource.definition.identity.resource_id
                for resource in _subject_references(project, value, owner=layer.resource)
            )
            sources.append(_layer_source(layer))
    selected = _apply_resource_edit(selected, invocation.subjects)
    if not _is_noop_edit(invocation.subjects):
        sources.append(_invocation_source(invocation))
    if not selected:
        raise PlanningProblem("planning.subjects.empty", resource=project.project, field="subjects")
    resources = tuple(_find_subject(project, item, project.project) for item in selected)
    if any(resource.definition.identity.kind is ResourceKind.SCENARIO for resource in resources):
        raise PlanningProblem("planning.scenario.not-supported", field="subjects")
    return resources, tuple(sources)


def _subject_references(
    project: CompiledProject,
    value: CanonicalValue,
    *,
    owner: CompiledResource,
) -> tuple[CompiledResource, ...]:
    if type(value) is not CanonicalArray:
        raise PlanningProblem("planning.field.shape-invalid", resource=owner, field="subjects")
    resources: list[CompiledResource] = []
    for item in value.items:
        if type(item) is not LinkedResource or item.target.identity.kind not in {
            ResourceKind.REQUEST,
            ResourceKind.SCENARIO,
        }:
            raise PlanningProblem(
                "planning.reference.kind-invalid", resource=owner, field="subjects"
            )
        resource = project.find(item.target.identity.kind, item.target.identity.resource_id)
        if resource is None or resource.definition != item.target:
            raise PlanningProblem(
                "planning.reference.inconsistent", resource=owner, field="subjects"
            )
        resources.append(resource)
    return tuple(resources)


def _find_subject(
    project: CompiledProject,
    resource_id: ResourceId,
    owner: CompiledResource,
) -> CompiledResource:
    matches = tuple(
        resource
        for kind in (ResourceKind.REQUEST, ResourceKind.SCENARIO)
        if (resource := project.find(kind, resource_id)) is not None
    )
    if len(matches) != 1:
        raise PlanningProblem("planning.subject.not-found-or-ambiguous", resource=owner)
    return matches[0]


def _validate_request_edit_targets(
    edits: tuple[InvocationRequestEdit, ...],
    subjects: tuple[CompiledResource, ...],
) -> None:
    selected = {subject.definition.identity.resource_id for subject in subjects}
    if any(edit.subject not in selected for edit in edits):
        raise PlanningProblem("planning.request-edit.subject-not-selected")
    if any(
        subject.definition.identity.kind is not ResourceKind.REQUEST
        for subject in subjects
        if subject.definition.identity.resource_id in {edit.subject for edit in edits}
    ):
        raise PlanningProblem("planning.request-edit.subject-kind-invalid")


def _request_edit(
    invocation: RunInvocation,
    subject: CompiledResource,
) -> InvocationRequestEdit | None:
    return next(
        (
            edit
            for edit in invocation.requests
            if edit.subject == subject.definition.identity.resource_id
        ),
        None,
    )


def _validate_environment_value_targets(
    invocation: RunInvocation,
    environments: tuple[CompiledResource, ...],
) -> None:
    """Reject invocation `$env` values outside the deliberately selected topology."""
    selected = {item.definition.identity.resource_id for item in environments}
    if any(item.environment not in selected for item in invocation.environment_values):
        raise PlanningProblem("planning.environment-value.environment-not-selected")


def _bind_topology(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    invocation: RunInvocation,
) -> tuple[
    tuple[CompiledResource, ...],
    tuple[EnvironmentPair, ...],
    tuple[PlanningSource, ...],
]:
    inherited: ComparisonTopology | AssertionTopology | None = None
    sources: list[PlanningSource] = []
    for layer in layers:
        baseline = object_member(layer.spec, "baseline")
        candidates = object_member(layer.spec, "candidates")
        environments = object_member(layer.spec, "environments")
        if any(item is not None for item in (baseline, candidates, environments)):
            inherited = _topology_from_layer(layer, baseline, candidates, environments)
            sources.append(_layer_source(layer))
    if type(invocation.topology) is InheritTopology:
        topology = inherited
    elif type(invocation.topology) is ComparisonTopologyEdit:
        topology = _edit_comparison_topology(inherited, invocation.topology)
    else:
        topology = invocation.topology
    if type(invocation.topology) is not InheritTopology:
        sources.append(_invocation_source(invocation))
    if topology is None:
        raise PlanningProblem("planning.topology.missing", resource=project.project)
    if type(topology) is ComparisonTopology:
        baseline_resource = find_resource(
            project,
            ResourceKind.ENVIRONMENT,
            topology.baseline,
            owner=project.project,
            field="baseline",
        )
        candidates_resources = tuple(
            find_resource(
                project,
                ResourceKind.ENVIRONMENT,
                candidate,
                owner=project.project,
                field="candidates",
            )
            for candidate in topology.candidates
        )
        environments_resources = (baseline_resource, *candidates_resources)
        pairs = tuple(
            EnvironmentPair(
                baseline=baseline_resource.definition,
                candidate=candidate.definition,
            )
            for candidate in candidates_resources
        )
        return environments_resources, pairs, tuple(sources)
    environments_resources = tuple(
        find_resource(
            project,
            ResourceKind.ENVIRONMENT,
            environment,
            owner=project.project,
            field="environments",
        )
        for environment in topology.environments
    )
    return environments_resources, (), tuple(sources)


def _edit_comparison_topology(
    inherited: ComparisonTopology | AssertionTopology | None,
    edit: ComparisonTopologyEdit,
) -> ComparisonTopology:
    if type(inherited) is not ComparisonTopology:
        raise PlanningProblem("planning.topology.comparison-edit-without-base")
    baseline = inherited.baseline
    if type(edit.baseline) is SetValue:
        baseline = edit.baseline.value
    elif type(edit.baseline) is Clear:
        raise PlanningProblem("planning.topology.incomplete", field="baseline")
    candidates = _apply_resource_edit(
        inherited.candidates,
        edit.candidates,
        duplicate_code="planning.topology.candidate-duplicate",
    )
    if not candidates:
        raise PlanningProblem("planning.topology.incomplete", field="candidates")
    try:
        return ComparisonTopology(baseline=baseline, candidates=candidates)
    except ValueError as error:
        raise PlanningProblem("planning.topology.contradictory") from error


def _topology_from_layer(
    layer: _Layer,
    baseline: CanonicalValue | None,
    candidates: CanonicalValue | None,
    environments: CanonicalValue | None,
) -> ComparisonTopology | AssertionTopology:
    if environments is not None:
        if baseline is not None or candidates is not None:
            raise PlanningProblem("planning.topology.contradictory", resource=layer.resource)
        return AssertionTopology(
            environments=_linked_ids(
                environments,
                ResourceKind.ENVIRONMENT,
                owner=layer.resource,
                field="environments",
            )
        )
    if baseline is None or candidates is None:
        raise PlanningProblem("planning.topology.incomplete", resource=layer.resource)
    baseline_ids = _linked_ids(
        baseline,
        ResourceKind.ENVIRONMENT,
        owner=layer.resource,
        field="baseline",
        multiple=False,
    )
    return ComparisonTopology(
        baseline=baseline_ids[0],
        candidates=_linked_ids(
            candidates,
            ResourceKind.ENVIRONMENT,
            owner=layer.resource,
            field="candidates",
        ),
    )


def _linked_ids(
    value: CanonicalValue,
    kind: ResourceKind,
    *,
    owner: CompiledResource,
    field: str,
    multiple: bool = True,
) -> tuple[ResourceId, ...]:
    items = value.items if multiple and type(value) is CanonicalArray else (value,)
    if multiple and type(value) is not CanonicalArray:
        raise PlanningProblem("planning.field.shape-invalid", resource=owner, field=field)
    result: list[ResourceId] = []
    for item in items:
        if type(item) is not LinkedResource or item.target.identity.kind is not kind:
            raise PlanningProblem("planning.reference.kind-invalid", resource=owner, field=field)
        result.append(item.target.identity.resource_id)
    if not result:
        raise PlanningProblem("planning.field.empty", resource=owner, field=field)
    return tuple(result)


def _bind_count(
    layers: tuple[_Layer, ...],
    override: object,
    *,
    field: str,
    default: int,
    invocation: RunInvocation,
) -> tuple[PositiveCount, tuple[PlanningSource, ...]]:
    value = default
    sources = [PlanningSource(kind=ProvenanceKind.VERSIONED_DEFAULT)]
    for layer in layers:
        configured = positive_integer(
            object_member(layer.spec, field), owner=layer.resource, field=field
        )
        if configured is not None:
            value = configured
            sources.append(_layer_source(layer))
    if type(override) is SetValue:
        value = override.value.value
        sources.append(_invocation_source(invocation))
    elif type(override) is Clear:
        value = default
        sources.append(_invocation_source(invocation))
    elif type(override) is not Inherit:
        raise TypeError("planner count override must be typed")
    return PositiveCount(value=value), tuple(sources)


def _bind_matrix_selections(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    invocation: RunInvocation,
) -> tuple[tuple[MatrixSelection, ...], tuple[PlanningSource, ...]]:
    selected: tuple[MatrixSelection, ...] = ()
    sources: list[PlanningSource] = [PlanningSource(kind=ProvenanceKind.VERSIONED_DEFAULT)]
    for layer in layers:
        value = object_member(layer.spec, "matrices")
        if value is not None:
            ids = _linked_ids(
                value,
                ResourceKind.MATRIX,
                owner=layer.resource,
                field="matrices",
            )
            selected = tuple(
                MatrixSelection(binding=MatrixBindingId(value=item.value), matrix=item)
                for item in ids
            )
            sources.append(_layer_source(layer))
    selected = _apply_matrix_edit(selected, invocation.matrices)
    if not _is_noop_edit(invocation.matrices):
        sources.append(_invocation_source(invocation))
    for selection in selected:
        find_resource(
            project,
            ResourceKind.MATRIX,
            selection.matrix,
            owner=project.project,
            field="matrices",
        )
    return selected, tuple(sources)


def _subject_matrices(
    project: CompiledProject,
    subject: CompiledResource,
    common: tuple[MatrixSelection, ...],
    *,
    maximum_cells: int,
) -> tuple[EffectiveMatrix, ...]:
    request_value = object_member(resource_spec(subject), "matrices")
    request_ids = (
        ()
        if request_value is None
        else _linked_ids(
            request_value,
            ResourceKind.MATRIX,
            owner=subject,
            field="matrices",
        )
    )
    selections = (
        *common,
        *(
            MatrixSelection(binding=MatrixBindingId(value=item.value), matrix=item)
            for item in request_ids
        ),
    )
    bindings = tuple(item.binding for item in selections)
    if len(bindings) != len(set(bindings)):
        raise PlanningProblem(
            "planning.matrix.binding-duplicate", resource=subject, field="matrices"
        )
    matrices: list[EffectiveMatrix] = []
    cell_count = 1
    for selection in selections:
        resource = find_resource(
            project,
            ResourceKind.MATRIX,
            selection.matrix,
            owner=subject,
            field="matrices",
        )
        remaining = maximum_cells // cell_count
        matrix = compile_matrix(resource, selection.binding, maximum_cases=remaining)
        matrix = _filter_matrix_cases(matrix, selection.cases, subject)
        cell_count *= len(matrix.cases)
        matrices.append(matrix)
    result = tuple(matrices)
    validate_unqualified_fields(result, owner=subject)
    return result


def _filter_matrix_cases(
    matrix: EffectiveMatrix,
    selected: tuple[MatrixCaseId, ...],
    owner: CompiledResource,
) -> EffectiveMatrix:
    """Apply one invocation case filter while preserving authored Matrix order."""
    if not selected:
        return matrix
    requested = set(selected)
    available = {case.case_id for case in matrix.cases}
    if not requested <= available:
        raise PlanningProblem(
            "planning.matrix.case-not-found",
            resource=owner,
            field="matrices",
        )
    return msgspec.structs.replace(
        matrix,
        cases=tuple(case for case in matrix.cases if case.case_id in requested),
    )


def _effective_profiles(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    subject: CompiledResource,
    environments: tuple[CompiledResource, ...],
    overrides: InvocationProfileOverrides,
) -> EffectiveProfiles:
    sources = (*layers, _Layer(subject, resource_spec(subject), ProvenanceKind.PROJECT))
    assertion_roots = _edit_profile_roots(
        project,
        _multi_roots(project, sources, "assertions"),
        overrides.assertions,
        ResourceKind.ASSERTION_PROFILE,
        subject,
    )
    diff_roots = _edit_profile_roots(
        project,
        _multi_roots(project, sources, "diffs"),
        overrides.diffs,
        ResourceKind.DIFF_PROFILE,
        subject,
    )
    assertions = _expand_profiles(project, assertion_roots, "include")
    diffs = _expand_profiles(project, diff_roots, "include")
    safety_roots = [*_single_or_multi_roots(project, sources, "safety")]
    safety_roots.extend(
        root
        for environment in environments
        for root in _single_or_multi_roots(
            project,
            (_Layer(environment, resource_spec(environment), ProvenanceKind.PROJECT),),
            "safety",
        )
    )
    effective_safety_roots = _edit_profile_roots(
        project,
        tuple(safety_roots),
        overrides.safety,
        ResourceKind.SAFETY_PROFILE,
        subject,
    )
    safety = _expand_profiles(project, effective_safety_roots, "extends", "include")
    gate = _edit_single_profile(
        project,
        _latest_root(project, sources, "gate"),
        overrides.gate,
        ResourceKind.GATE_PROFILE,
        subject,
    )
    redaction = _edit_single_profile(
        project,
        _latest_root(project, sources, "redaction"),
        overrides.redaction,
        ResourceKind.REDACTION_PROFILE,
        subject,
    )
    artifacts = _edit_single_profile(
        project,
        _latest_root(project, sources, "artifacts"),
        overrides.artifacts,
        ResourceKind.ARTIFACT_PROFILE,
        subject,
    )
    return EffectiveProfiles(
        assertions=tuple(_profile_handle(item) for item in assertions),
        diffs=tuple(_profile_handle(item) for item in diffs),
        gate=None if gate is None else _profile_handle(gate),
        safety=tuple(_profile_handle(item) for item in safety),
        redaction=None if redaction is None else _profile_handle(redaction),
        artifacts=None if artifacts is None else _profile_handle(artifacts),
    )


def _edit_profile_roots(
    project: CompiledProject,
    inherited: tuple[ProfileResource, ...],
    edit: CollectionOverridePlan[ResourceId, ResourceId],
    kind: ResourceKind,
    owner: CompiledResource,
) -> tuple[ProfileResource, ...]:
    if type(edit.base) is KeepBase:
        values = list(inherited)
    elif type(edit.base) is ClearBase:
        values = []
    else:
        values = [
            find_resource(project, kind, resource_id, owner=owner, field="profiles")
            for resource_id in edit.base.items
        ]
    values = [
        value
        for value in values
        if _named_profile_id(value) is None or _named_profile_id(value) not in edit.remove
    ]
    for operation in edit.set_items:
        values = [value for value in values if _named_profile_id(value) != operation.identity]
        values.append(find_resource(project, kind, operation.item, owner=owner, field="profiles"))
    values.extend(
        find_resource(project, kind, resource_id, owner=owner, field="profiles")
        for resource_id in edit.add
    )
    return tuple(values)


def _edit_single_profile(
    project: CompiledProject,
    inherited: ProfileResource | None,
    edit: object,
    kind: ResourceKind,
    owner: CompiledResource,
) -> ProfileResource | None:
    if type(edit) is Inherit:
        return inherited
    if type(edit) is Clear:
        return None
    if type(edit) is SetValue and type(edit.value) is ResourceId:
        return find_resource(project, kind, edit.value, owner=owner, field="profiles")
    raise TypeError("single profile edit must inherit, clear, or set ResourceId")


def _named_profile_id(resource: ProfileResource) -> ResourceId | None:
    if type(resource) is CompiledResource:
        return resource.definition.identity.resource_id
    return None


def _all_safety_resources(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    subjects: tuple[CompiledResource, ...],
    environments: tuple[CompiledResource, ...],
    override: CollectionOverridePlan[ResourceId, ResourceId],
) -> tuple[ProfileResource, ...]:
    roots: list[ProfileResource] = []
    roots.extend(_single_or_multi_roots(project, layers, "safety"))
    for resource in (*subjects, *environments):
        roots.extend(
            _single_or_multi_roots(
                project,
                (_Layer(resource, resource_spec(resource), ProvenanceKind.PROJECT),),
                "safety",
            )
        )
    edited = _edit_profile_roots(
        project,
        tuple(roots),
        override,
        ResourceKind.SAFETY_PROFILE,
        project.project,
    )
    return _expand_profiles(project, edited, "extends", "include")


def _multi_roots(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    field: str,
) -> tuple[ProfileResource, ...]:
    return tuple(root for layer in layers for root in _roots(project, layer, field, multiple=True))


def _single_or_multi_roots(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    field: str,
) -> tuple[ProfileResource, ...]:
    return tuple(root for layer in layers for root in _roots(project, layer, field, multiple=False))


def _latest_root(
    project: CompiledProject,
    layers: tuple[_Layer, ...],
    field: str,
) -> ProfileResource | None:
    result: ProfileResource | None = None
    for layer in layers:
        roots = _roots(project, layer, field, multiple=False)
        if roots:
            result = roots[0]
    return result


def _roots(
    project: CompiledProject,
    layer: _Layer,
    field: str,
    *,
    multiple: bool,
) -> tuple[ProfileResource, ...]:
    value = object_member(layer.spec, field)
    if value is None:
        return ()
    items = value.items if multiple and type(value) is CanonicalArray else (value,)
    if multiple and type(value) is not CanonicalArray:
        raise PlanningProblem("planning.field.shape-invalid", resource=layer.resource, field=field)
    result: list[ProfileResource] = []
    for item in items:
        if type(item) is InlineResource:
            result.append(item)
        elif type(item) is LinkedResource:
            result.append(_linked_profile(project, layer.resource, item))
        else:
            raise PlanningProblem("planning.profile.invalid", resource=layer.resource, field=field)
    return tuple(result)


def _linked_profile(
    project: CompiledProject,
    owner: CompiledResource,
    link: LinkedResource,
) -> CompiledResource:
    resource = project.find(
        link.target.identity.kind,
        link.target.identity.resource_id,
    )
    if resource is None or resource.definition != link.target:
        raise PlanningProblem("planning.profile.inconsistent", resource=owner)
    return resource


def _expand_profiles(
    project: CompiledProject,
    roots: tuple[ProfileResource, ...],
    *include_fields: str,
) -> tuple[ProfileResource, ...]:
    result: list[ProfileResource] = []
    visited: set[DefinitionHandle] = set()
    active: set[DefinitionHandle] = set()

    def visit(profile: ProfileResource) -> None:
        handle = _profile_handle(profile)
        if handle in visited:
            return
        if handle in active:
            raise PlanningProblem("planning.profile.cycle")
        active.add(handle)
        spec = resource_spec(profile) if type(profile) is CompiledResource else profile.spec
        for field in include_fields:
            value = object_member(spec, field)
            if value is None:
                continue
            values = value.items if type(value) is CanonicalArray else (value,)
            for item in values:
                if type(item) is not LinkedResource:
                    raise PlanningProblem("planning.profile.include-invalid", field=field)
                target = project.find(item.target.identity.kind, item.target.identity.resource_id)
                if target is None or target.definition != item.target:
                    raise PlanningProblem("planning.profile.include-inconsistent", field=field)
                visit(target)
        active.remove(handle)
        visited.add(handle)
        result.append(profile)

    for root in roots:
        visit(root)
    return tuple(result)


def _value_layers(
    project: CompiledProject,
    project_spec: CanonicalObject,
    defaults: CanonicalObject,
    execution_profile: CompiledResource | None,
    environments: tuple[CompiledResource, ...],
    invocation: RunInvocation,
) -> tuple[ValueBindingLayer, ...]:
    layers: list[ValueBindingLayer] = []
    _append_value_layer(
        layers,
        source=PlanningSource(kind=ProvenanceKind.PROJECT, definition=project.project.definition),
        spec=project_spec,
    )
    _append_value_layer(
        layers,
        source=PlanningSource(kind=ProvenanceKind.PROJECT, definition=project.project.definition),
        spec=defaults,
    )
    for environment in environments:
        _append_value_layer(
            layers,
            source=PlanningSource(kind=ProvenanceKind.PROJECT, definition=environment.definition),
            spec=resource_spec(environment),
            environment=environment.definition,
        )
    if execution_profile is not None:
        _append_value_layer(
            layers,
            source=PlanningSource(
                kind=ProvenanceKind.PROFILE, definition=execution_profile.definition
            ),
            spec=resource_spec(execution_profile),
        )
    selected_environment_ids = {
        environment.definition.identity.resource_id for environment in environments
    }
    if any(
        overlay.environment is not None and overlay.environment not in selected_environment_ids
        for overlay in invocation.values
    ):
        raise PlanningProblem(
            "planning.values.environment-not-selected",
            resource=project.project,
            field="values",
        )
    ordered_overlays = (
        *(overlay for overlay in invocation.values if overlay.environment is None),
        *(
            overlay
            for environment in environments
            for overlay in invocation.values
            if overlay.environment == environment.definition.identity.resource_id
        ),
    )
    for overlay in ordered_overlays:
        overlay_environment = (
            None
            if overlay.environment is None
            else find_resource(
                project,
                ResourceKind.ENVIRONMENT,
                overlay.environment,
                owner=project.project,
                field="values",
            ).definition
        )
        layers.append(
            ValueBindingLayer(
                source=_invocation_source(invocation),
                environment=overlay_environment,
                parameters=overlay.parameters,
                variables=overlay.variables,
                secrets=overlay.secrets,
            )
        )
    return tuple(layers)


def _append_value_layer(
    layers: list[ValueBindingLayer],
    *,
    source: PlanningSource,
    spec: CanonicalObject,
    environment: DefinitionRef | None = None,
) -> None:
    values = tuple(object_member(spec, field) for field in ("parameters", "variables", "secrets"))
    if all(value is None for value in values):
        return
    if any(value is not None and not is_value_expression(value) for value in values):
        raise PlanningProblem("planning.values.expression-invalid")
    layers.append(
        ValueBindingLayer(
            source=source,
            environment=environment,
            parameters=_expression(values[0]),
            variables=_expression(values[1]),
            secrets=_expression(values[2]),
        )
    )


def _expression(value: CanonicalValue | None) -> ValueExpression | None:
    return value if value is not None and is_value_expression(value) else None


def _authorize_selection(
    subjects: tuple[CompiledResource, ...],
    environments: tuple[CompiledResource, ...],
    authority: PlanningAuthority,
) -> None:
    if authority.allowed_subjects is not None and any(
        resource.definition.identity not in authority.allowed_subjects for resource in subjects
    ):
        raise PlanningProblem("planning.policy.subject-denied")
    if authority.allowed_environments is not None and any(
        resource.definition.identity not in authority.allowed_environments
        for resource in environments
    ):
        raise PlanningProblem("planning.policy.environment-denied")


def _apply_resource_edit(
    inherited: tuple[ResourceId, ...],
    edit: CollectionOverridePlan[ResourceId, ResourceId],
    *,
    duplicate_code: str = "planning.subjects.duplicate",
) -> tuple[ResourceId, ...]:
    values = list(_edit_base(inherited, edit))
    values = [value for value in values if value not in edit.remove]
    for operation in edit.set_items:
        values = [value for value in values if value != operation.identity]
        values.append(operation.item)
    values.extend(edit.add)
    if len(values) != len(set(values)):
        raise PlanningProblem(duplicate_code)
    return tuple(values)


def _apply_matrix_edit(
    inherited: tuple[MatrixSelection, ...],
    edit: CollectionOverridePlan[MatrixBindingId, MatrixSelection],
) -> tuple[MatrixSelection, ...]:
    values = list(_edit_base(inherited, edit))
    values = [value for value in values if value.binding not in edit.remove]
    for operation in edit.set_items:
        values = [value for value in values if value.binding != operation.identity]
        values.append(operation.item)
    values.extend(edit.add)
    bindings = tuple(item.binding for item in values)
    if len(bindings) != len(set(bindings)):
        raise PlanningProblem("planning.matrix.binding-duplicate")
    return tuple(values)


def _edit_base[IdentityT: ImmutableDomainValue, ItemT: ImmutableDomainValue](
    inherited: tuple[ItemT, ...],
    edit: CollectionOverridePlan[IdentityT, ItemT],
) -> tuple[ItemT, ...]:
    if type(edit.base) is KeepBase:
        return inherited
    if type(edit.base) is ClearBase:
        return ()
    return edit.base.items


def _is_noop_edit[IdentityT: ImmutableDomainValue, ItemT: ImmutableDomainValue](
    edit: CollectionOverridePlan[IdentityT, ItemT],
) -> bool:
    return type(edit.base) is KeepBase and not edit.remove and not edit.set_items and not edit.add


def _layer_source(layer: _Layer) -> PlanningSource:
    return PlanningSource(kind=layer.source_kind, definition=layer.resource.definition)


def _invocation_source(invocation: RunInvocation) -> PlanningSource:
    return PlanningSource(kind=ProvenanceKind.INVOCATION, invocation_id=invocation.invocation_id)


def _profile_handle(profile: ProfileResource) -> DefinitionHandle:
    return profile.definition


def _profile_source(profile: ProfileResource) -> PlanningSource:
    handle = _profile_handle(profile)
    kind = (
        ProvenanceKind.SAFETY_PROFILE
        if (handle.identity.kind if type(handle) is DefinitionRef else handle.kind)
        is ResourceKind.SAFETY_PROFILE
        else ProvenanceKind.PROFILE
    )
    return PlanningSource(kind=kind, definition=handle)
