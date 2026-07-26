"""Effect-free projection from admitted execution state to safe Plan inspection."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from comparo.application.evaluation_binding import EvaluationPreflightResult
from comparo.application.plan_inspection_authority import PlanArtifactAuthorityInspection
from comparo.application.plan_inspection_authority import PlanHttpAuthorityInspection
from comparo.application.plan_inspection_authority import PlanSecretEgressRuleInspection
from comparo.application.plan_inspection_authority import PlanSecretEgressSourceInspection
from comparo.application.plan_inspection_authority import PlanValueAuthorityInspection
from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_inspection_profiles import PlanGateInspection
from comparo.application.plan_inspection_profiles import PlanGateThresholdInspection
from comparo.application.plan_inspection_profiles import PlanProfileAttachmentInspection
from comparo.application.plan_inspection_profiles import PlanProfilesInspection
from comparo.application.plan_inspection_profiles import PlanValidationInspection
from comparo.application.plan_inspection_topology import BudgetInspection
from comparo.application.plan_inspection_topology import EnvironmentPairInspection
from comparo.application.plan_inspection_topology import InlineSourceCoordinate
from comparo.application.plan_inspection_topology import MatrixAxisInspection
from comparo.application.plan_inspection_topology import PlanCardinalityInspection
from comparo.application.plan_inspection_topology import PlanInspection
from comparo.application.plan_inspection_topology import PlanningSourceInspection
from comparo.application.plan_inspection_topology import PlanSliceInspection
from comparo.application.plan_inspection_topology import ProvenanceInspection
from comparo.application.plan_sources import inspect_sensitive_sources
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import is_read_only_http_method
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning import PlanSliceCardinality
from comparo.core.domain.planning import RunPlanCardinality
from comparo.core.domain.primitives import Count
from comparo.core.domain.values import JsonString
from comparo.core.planning.models import EffectiveProfiles
from comparo.core.planning.models import EffectiveSubject
from comparo.core.planning.models import PlanningSource
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.models import is_definition_handle
from comparo.core.policy import CompiledExecutionPolicy


def inspect_plan(
    prepared: PreparedRun,
    policy: CompiledExecutionPolicy | None = None,
    evaluation: EvaluationPreflightResult | None = None,
    artifact_root: Path | None = None,
) -> PlanInspection:
    """Project an admitted run to a deterministic, unresolved inspection document."""
    if type(prepared) is not PreparedRun:
        raise TypeError("plan inspection requires PreparedRun")
    if policy is not None:
        if type(policy) is not CompiledExecutionPolicy:
            raise TypeError("plan inspection policy must be compiled or absent")
        if policy.definition != prepared.authority.policy:
            raise ValueError("plan inspection policy differs from planning authority")
    if evaluation is not None:
        if type(evaluation) is not EvaluationPreflightResult:
            raise TypeError("plan evaluation preflight must be typed or absent")
        if not evaluation.supported:
            raise ValueError("plan inspection cannot retain rejected evaluation preflight")
    if artifact_root is not None and not isinstance(artifact_root, Path):
        raise TypeError("plan artifact root must be a Path or absent")
    if artifact_root is not None and not artifact_root.is_absolute():
        raise ValueError("plan artifact root must be resolved and absolute")
    plan = prepared.plan
    subjects = {item.definition: item for item in prepared.effective.subjects}
    resources = {item.definition: item for item in prepared.project.resources}
    slices = tuple(
        _inspect_slice(
            item,
            subjects[item.cells.subject],
            resources.get(item.cells.subject),
        )
        for item in plan.slices
    )
    return PlanInspection(
        api_version="comparo.plan/v1alpha1",
        run_id=str(plan.run_id.value),
        invocation_id=str(prepared.invocation.invocation_id.value),
        fingerprint=plan.fingerprint.digest.hex_value,
        ordering=plan.ordering.value,
        policy=_definition(prepared.authority.policy),
        cardinality=_cardinality(plan.cardinality),
        environment_count=len(prepared.effective.environments),
        slices=slices,
        write_capable_subjects=sum(item.unsafe_method is True for item in slices),
        budgets=tuple(
            BudgetInspection(
                resource=item.resource.value,
                requested=cast("Count", item.requested).value,
                effective=cast("Count", item.effective).value,
                cap=cast("Count", item.cap).value,
            )
            for item in plan.budgets
        ),
        provenance=tuple(
            ProvenanceInspection(
                field=item.field,
                sources=tuple(_inspect_planning_source(source) for source in item.sources),
            )
            for item in prepared.effective.provenance
        ),
        validation=None if evaluation is None else _validation(evaluation),
        sensitive_sources=inspect_sensitive_sources(prepared),
        http_authority=None if policy is None else _http_authority(policy),
        value_authority=None if policy is None else _value_authority(policy),
        artifact_authority=(None if policy is None else _artifact_authority(policy, artifact_root)),
    )


def _validation(value: EvaluationPreflightResult) -> PlanValidationInspection:
    gate = value.gate
    inspected_gate = None
    if gate is not None:
        inspected_gate = PlanGateInspection(
            profile=_definition(gate.definition),
            severities=tuple(item.value for item in gate.severities),
            environment_roles=tuple(item.value for item in gate.environment_roles),
            require_complete_run=gate.require_complete_run,
            require_assertion_coverage=gate.require_assertion_coverage,
            require_diff_coverage=gate.require_diff_coverage,
            thresholds=tuple(
                PlanGateThresholdInspection(
                    severity=item.severity.value,
                    violated=item.violated_rules.value,
                    drifted=item.drifted_rules.value,
                    unavailable=item.unavailable_rules.value,
                    incomparable=item.incomparable_rules.value,
                    incomplete=item.incomplete_rules.value,
                    errors=item.error_rules.value,
                )
                for item in gate.thresholds
            ),
        )
    return PlanValidationInspection(
        assertion_rules=value.assertion_rules,
        diff_rules=value.diff_rules,
        gate=inspected_gate,
    )


def _inspect_slice(
    value: PlanSlice,
    subject: EffectiveSubject,
    resource: CompiledResource | None,
) -> PlanSliceInspection:
    authored_method = (
        None if resource is None else _canonical_string(resource.spec, ("request", "method"))
    )
    method = None if authored_method is None else authored_method.upper()
    return PlanSliceInspection(
        subject=_definition(value.cells.subject),
        matrices=tuple(
            MatrixAxisInspection(
                binding=axis.binding.value,
                matrix=_definition(axis.matrix),
                cases=tuple(case.value for case in axis.cases),
            )
            for axis in value.cells.matrix.axes
        ),
        trials=value.trial_count.value,
        environments=tuple(_definition(item) for item in value.environments),
        comparisons=tuple(
            EnvironmentPairInspection(
                baseline=_definition(item.baseline),
                candidate=_definition(item.candidate),
            )
            for item in value.comparisons
        ),
        maximum_attempts_per_execution=value.maximum_attempts_per_execution.value,
        cardinality=_cardinality(value.cardinality),
        method=method,
        unsafe_method=_unsafe_method(method),
        profiles=_profiles(subject.profiles),
    )


def _http_authority(policy: CompiledExecutionPolicy) -> PlanHttpAuthorityInspection:
    value = policy.http
    return PlanHttpAuthorityInspection(
        allowed_schemes=tuple(item.value for item in value.allowed_schemes),
        allowed_methods=tuple(item.value for item in value.allowed_methods),
        allowed_hosts=tuple(item.value for item in value.allowed_hosts),
        allowed_ports=tuple(item.value for item in value.allowed_ports),
        allow_any_host=value.allow_any_host,
        allow_any_port=value.allow_any_port,
        require_mutation_approval=value.require_mutation_approval,
        allowed_address_classes=tuple(item.value for item in value.allowed_address_classes),
        allow_proxy_dns=value.allow_proxy_dns,
    )


def _value_authority(policy: CompiledExecutionPolicy) -> PlanValueAuthorityInspection:
    value = policy.values
    return PlanValueAuthorityInspection(
        ambient_environment_names=value.ambient_environment.names,
        allow_any_ambient_environment=value.ambient_environment.allow_any,
        allow_project_files=value.allow_project_files,
        remote_schema_sources=tuple(item.identifier for item in value.remote_schemas.sources),
        allow_any_remote_schemas=value.remote_schemas.allow_any,
        secret_egress=tuple(
            PlanSecretEgressRuleInspection(
                sources=tuple(
                    PlanSecretEgressSourceInspection(
                        kind=source.kind.value,
                        name=".".join(source.symbol.segments),
                    )
                    for source in rule.sources
                ),
                allowed_schemes=tuple(item.value for item in rule.allowed_schemes),
                allowed_methods=tuple(item.value for item in rule.allowed_methods),
                allowed_hosts=tuple(item.value for item in rule.allowed_hosts),
                allowed_ports=tuple(item.value for item in rule.allowed_ports),
            )
            for rule in value.secret_egress.rules
        ),
        allow_any_secret_egress=value.secret_egress.allow_any,
    )


def _artifact_authority(
    policy: CompiledExecutionPolicy,
    selected: Path | None,
) -> PlanArtifactAuthorityInspection:
    authorized = False
    if selected is not None:
        try:
            prospective = selected.resolve(strict=False)
        except OSError:
            prospective = None
        if prospective is not None:
            authorized = policy.artifacts.allow_sanitized_persistence and any(
                prospective.is_relative_to(Path(item)) for item in policy.artifacts.roots
            )
    return PlanArtifactAuthorityInspection(
        allow_sanitized_persistence=policy.artifacts.allow_sanitized_persistence,
        root_count=len(policy.artifacts.roots),
        selected_root_present=selected is not None,
        selected_root_authorized=authorized,
    )


def _inspect_planning_source(source: PlanningSource) -> PlanningSourceInspection:
    definition = source.definition
    if type(definition) is DefinitionRef:
        return PlanningSourceInspection(
            kind=source.kind.value,
            definition=_definition(definition),
        )
    if type(definition) is InlineDefinitionRef:
        owner = definition.origin.owner
        return PlanningSourceInspection(
            kind=source.kind.value,
            inline=InlineSourceCoordinate(
                owner_namespace=owner.namespace.value,
                owner_kind=owner.kind.value,
                owner_resource_id=owner.resource_id.value,
                fragment=definition.origin.fragment.value,
                fingerprint=definition.fingerprint.digest.hex_value,
            ),
        )
    return PlanningSourceInspection(
        kind=source.kind.value,
        invocation_id=(None if source.invocation_id is None else str(source.invocation_id.value)),
    )


def _profiles(value: object) -> PlanProfilesInspection:
    if type(value) is not EffectiveProfiles:
        raise TypeError("plan profile inspection requires EffectiveProfiles")
    return PlanProfilesInspection(
        assertions=tuple(_inspect_profile_handle(item) for item in value.assertions),
        diffs=tuple(_inspect_profile_handle(item) for item in value.diffs),
        gate=None if value.gate is None else _inspect_profile_handle(value.gate),
        safety=tuple(_inspect_profile_handle(item) for item in value.safety),
        redaction=None if value.redaction is None else _inspect_profile_handle(value.redaction),
        artifacts=None if value.artifacts is None else _inspect_profile_handle(value.artifacts),
    )


def _unsafe_method(method: str | None) -> bool | None:
    """Classify a canonical method without making inspection a new failure boundary."""
    if method is None:
        return None
    try:
        validated = HttpMethod(value=method)
    except ValueError:
        return None
    return not is_read_only_http_method(validated)


def _inspect_profile_handle(value: object) -> PlanProfileAttachmentInspection:
    if not is_definition_handle(value):
        raise TypeError("plan profile handle must be a definition handle")
    if type(value) is DefinitionRef:
        identity = value.identity
        return PlanProfileAttachmentInspection(
            kind=identity.kind.value,
            namespace=identity.namespace.value,
            resource_id=identity.resource_id.value,
            fingerprint=value.fingerprint.digest.hex_value,
            inline=False,
        )
    inline = value
    owner = inline.origin.owner
    return PlanProfileAttachmentInspection(
        kind=inline.kind.value,
        namespace=None,
        resource_id=None,
        fingerprint=inline.fingerprint.digest.hex_value,
        inline=True,
        owner=(owner.namespace.value, owner.kind.value, owner.resource_id.value),
        fragment=inline.origin.fragment.value,
    )


def _canonical_string(value: object, path: tuple[str, ...]) -> str | None:
    current = value
    for segment in path:
        if type(current) is CanonicalObject:
            member = next((item for item in current.members if item.name == segment), None)
            if member is None:
                return None
            current = member.value
            continue
        if type(current) is ObjectExpr:
            expression_member = next(
                (item for item in current.members if item.name == segment),
                None,
            )
            if expression_member is None:
                return None
            current = expression_member.expression
            continue
        return None
    if type(current) is LiteralExpr:
        current = current.value
    if type(current) is not JsonString:
        return None
    return current.value


def _definition(value: DefinitionRef) -> ResourceCoordinate:
    identity = value.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=value.fingerprint.digest.hex_value,
    )


def _cardinality(
    value: PlanSliceCardinality | RunPlanCardinality,
) -> PlanCardinalityInspection:
    return PlanCardinalityInspection(
        cells=value.cells.value,
        trials=value.trials.value,
        environment_executions=value.environment_executions.value,
        comparisons=value.comparisons.value,
        maximum_attempts=value.maximum_attempts.value,
    )


__all__ = ("inspect_plan",)
