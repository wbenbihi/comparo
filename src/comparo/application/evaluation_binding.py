"""Fail-closed configuration binding for the initial production evaluation subset.

The v1alpha1 frontend deliberately retains rule bodies as value-expression trees.  This module is
the adapter boundary that lowers the small, explicitly supported authored subset to the pure
assertion, diff, and gate kernels.  Everything else is rejected while the run is still offline.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import final
from uuid import uuid5

import msgspec

from comparo.core.assertions.accounting import AssertionBudgets
from comparo.core.assertions.accounting import assertion_budget_operations
from comparo.core.assertions.engine import AssertionControl
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.inputs import PreparedExpression as PreparedAssertionExpression
from comparo.core.assertions.inputs import PreparedOperandError as AssertionOperandError
from comparo.core.assertions.inputs import PreparedOperandReady as AssertionOperandReady
from comparo.core.assertions.inputs import PreparedOperandUnavailable as AssertionOperandUnavailable
from comparo.core.assertions.inputs import required_expressions as required_assertion_expressions
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.diffs.accounting import DiffBudgets
from comparo.core.diffs.accounting import DiffCancellation
from comparo.core.diffs.accounting import DiffControl
from comparo.core.diffs.accounting import diff_budget_operations
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.inputs import PreparedExpression as PreparedDiffExpression
from comparo.core.diffs.inputs import PreparedOperandError as DiffOperandError
from comparo.core.diffs.inputs import PreparedOperandReady as DiffOperandReady
from comparo.core.diffs.inputs import PreparedOperandUnavailable as DiffOperandUnavailable
from comparo.core.diffs.inputs import required_expressions as required_diff_expressions
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import AssertionRuleDefinition
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budget_origins import InvocationBudgetOrigin
from comparo.core.domain.budget_origins import VersionedDefaultSetRef
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticContext
from comparo.core.domain.diagnostics import DiagnosticPathSegment
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.diagnostics import IndexPathSegment
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import DiffRuleDefinition
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.rules import DirectedEnvironmentPair
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import IgnoreComparator
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.model import GateThreshold
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import SourceLocation
from comparo.core.domain.scopes import ComparisonBudgetScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import Unavailable
from comparo.core.planning._canonical import object_member
from comparo.core.planning.models import DefinitionHandle
from comparo.core.planning.models import EffectiveSubject
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.context import RunContext
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.snapshots import cell_inputs
from comparo.core.runtime.models import ComparisonWork
from comparo.core.runtime.models import ExecutionRecord
from comparo.core.runtime.models import ObservedExecution
from comparo.core.runtime.ports import AssertionCall
from comparo.core.runtime.ports import DiffCall
from comparo.core.selection import SelectionBudgets
from comparo.core.selection import SelectionCancellation
from comparo.core.selection import SelectionControl
from comparo.core.selection import selection_budget_operations

_PROFILE_EMPTY = DiagnosticCode(value="evaluation.profile.empty")
_PROFILE_FIELD = DiagnosticCode(value="evaluation.profile.field-unsupported")
_PROFILE_MISSING = DiagnosticCode(value="evaluation.profile.inconsistent")
_RULE_SHAPE = DiagnosticCode(value="evaluation.rule.shape-unsupported")
_RULE_FIELD = DiagnosticCode(value="evaluation.rule.field-unsupported")
_RULE_ID = DiagnosticCode(value="evaluation.rule.id-invalid")
_RULE_DUPLICATE = DiagnosticCode(value="evaluation.rule.id-duplicate")
_SELECTOR = DiagnosticCode(value="evaluation.selector.unsupported")
_OPERATOR = DiagnosticCode(value="evaluation.operator.unsupported")
_GATE = DiagnosticCode(value="evaluation.gate.unsupported")
_GATE_MULTIPLE = DiagnosticCode(value="evaluation.gate.multiple")
_INLINE_GATE = DiagnosticCode(value="evaluation.gate.inline-unsupported")
_RESOLUTION_UNAVAILABLE = DiagnosticCode(value="evaluation.operand.unavailable")
_RESOLUTION_ERROR = DiagnosticCode(value="evaluation.operand.error")
_CANCELLED = DiagnosticCode(value="evaluation.cancelled")

_ASSERTION_FIELDS = frozenset(
    {"id", "selector", "operator", "severity", "quantifier", "completeness"}
)
_DIFF_FIELDS = frozenset({"id", "selector", "comparator", "severity", "completeness"})
_PROFILE_FIELDS = frozenset({"include", "rules"})
_SUPPORTED_ASSERTIONS = frozenset({"equals", "notEquals"})
_SUPPORTED_COMPARATORS = frozenset({"exact", "ignore"})
_JSON_KINDS = tuple(sorted(JSON_VALUE_KINDS, key=lambda item: item.value))


class EvaluationCapabilityFamily(DomainStrEnum):
    """Closed authored capability families reported by offline preflight."""

    ASSERTION_OPERATOR = "assertion-operator"
    DIFF_TRANSFORM = "diff-transform"
    DIFF_REDUCER = "diff-reducer"
    DIFF_PAIRING = "diff-pairing"
    DIFF_COMPARATOR = "diff-comparator"
    SELECTOR = "selector"
    GATE = "gate"
    PROFILE_COMPOSITION = "profile-composition"


@final
class RejectedEvaluationCapability(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free unsupported form or closed public alias found by preflight."""

    family: EvaluationCapabilityFamily
    alias: str | None
    resource: ResourceIdentity
    rule_index: Count | None = None

    def __post_init__(self) -> None:
        """Require only stable catalog names and source-safe coordinates."""
        if type(self.family) is not EvaluationCapabilityFamily:
            raise TypeError("rejected capability family must be typed")
        if self.alias is not None and (type(self.alias) is not str or len(self.alias) > 64):
            raise ValueError("rejected capability alias must be bounded text")
        if type(self.resource) is not ResourceIdentity:
            raise TypeError("rejected capability resource must be a ResourceIdentity")
        if self.rule_index is not None and type(self.rule_index) is not Count:
            raise TypeError("rejected capability rule index must be a Count")


@final
class EvaluationRuleLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite caller-owned limits independently applied to every pure rule invocation."""

    selection_work: int
    selection_matches: int
    selected_result_bytes: int
    transform_work: int
    facts: int
    evidence_examples: int
    evidence_bytes: int
    derived_values: int
    derived_bytes: int
    pairs: int

    def __post_init__(self) -> None:
        """Reject ambient, absent, boolean, zero, and negative limit values."""
        if any(type(value) is not int or value < 1 for value in self._values()):
            raise ValueError("evaluation rule limits must be positive exact integers")

    def _values(self) -> tuple[int, ...]:
        return (
            self.selection_work,
            self.selection_matches,
            self.selected_result_bytes,
            self.transform_work,
            self.facts,
            self.evidence_examples,
            self.evidence_bytes,
            self.derived_values,
            self.derived_bytes,
            self.pairs,
        )


@final
class EvaluationBindingResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one prepared evaluation adapter or safe preflight diagnostics."""

    preparation: ConfigurationEvaluationPreparation | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    rejected: tuple[RejectedEvaluationCapability, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful and failed outcomes disjoint and deterministically ordered."""
        if (
            self.preparation is not None
            and type(self.preparation) is not ConfigurationEvaluationPreparation
        ):
            raise TypeError("evaluation binding result requires a typed preparation")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not Diagnostic for item in self.diagnostics
        ):
            raise TypeError("evaluation binding diagnostics must be an immutable typed tuple")
        if type(self.rejected) is not tuple or any(
            type(item) is not RejectedEvaluationCapability for item in self.rejected
        ):
            raise TypeError("rejected evaluation capabilities must be an immutable typed tuple")
        if (self.preparation is None) == (not self.diagnostics):
            raise ValueError("evaluation binding must contain exactly preparation or diagnostics")
        if self.preparation is not None and self.rejected:
            raise ValueError("successful evaluation binding cannot reject capabilities")


@final
class _SubjectRules(msgspec.Struct, frozen=True, kw_only=True):
    subject: DefinitionRef
    assertions: tuple[CompiledAssertionRule, ...]
    diffs: tuple[CompiledDiffRule, ...]


class _Problems:
    __slots__ = ("diagnostics", "rejected")

    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []
        self.rejected: list[RejectedEvaluationCapability] = []

    def add(
        self,
        code: DiagnosticCode,
        profile: CompiledResource | InlineResource,
        *,
        rule_index: int | None = None,
        field: str | None = None,
        family: EvaluationCapabilityFamily | None = None,
        alias: str | None = None,
    ) -> None:
        resource, location = _profile_context(profile)
        path: list[DiagnosticPathSegment] = []
        if rule_index is not None:
            path.extend(
                (
                    FieldPathSegment(name="rules"),
                    IndexPathSegment(index=Count(value=rule_index)),
                )
            )
        if field is not None:
            path.append(FieldPathSegment(name=field))
        self.diagnostics.append(
            Diagnostic(
                code=code,
                severity=DiagnosticSeverity.ERROR,
                stage=DiagnosticStage.COMPILATION,
                context=DiagnosticContext(
                    resource=resource,
                    location=location,
                    field_path=tuple(path),
                ),
            )
        )
        if family is not None:
            self.rejected.append(
                RejectedEvaluationCapability(
                    family=family,
                    alias=alias,
                    resource=resource,
                    rule_index=None if rule_index is None else Count(value=rule_index),
                )
            )


class ConfigurationEvaluationPreparation:
    """Production runtime adapter for already preflighted configuration evaluation."""

    __slots__ = ("_context", "_gate", "_limits", "_prepared", "_subjects")

    def __init__(
        self,
        *,
        prepared: PreparedRun,
        context: RunContext,
        limits: EvaluationRuleLimits,
        subjects: tuple[_SubjectRules, ...],
        gate: CompiledGateProfile | None,
    ) -> None:
        """Bind one admitted run, its context, finite limits, rules, and optional gate."""
        if type(prepared) is not PreparedRun or type(context) is not RunContext:
            raise TypeError("configuration evaluation requires PreparedRun and RunContext")
        if type(limits) is not EvaluationRuleLimits:
            raise TypeError("configuration evaluation requires EvaluationRuleLimits")
        if type(subjects) is not tuple or any(type(item) is not _SubjectRules for item in subjects):
            raise TypeError("configuration evaluation subjects must be an immutable typed tuple")
        if gate is not None and type(gate) is not CompiledGateProfile:
            raise TypeError("configuration evaluation gate must be compiled")
        if context.run.run_id != prepared.plan.run_id:
            raise ValueError("configuration evaluation context belongs to another run")
        self._prepared = prepared
        self._context = context
        self._limits = limits
        self._subjects = subjects
        self._gate = gate

    @property
    def gate_profile(self) -> CompiledGateProfile | None:
        """Return the single preflighted run gate, if requested."""
        return self._gate

    async def assertions(
        self,
        prepared: PreparedRun,
        record: ExecutionRecord,
    ) -> tuple[AssertionCall, ...]:
        """Resolve exact execution-scoped operands and bind real assertion applications."""
        self._require_prepared(prepared)
        if type(record) is not ExecutionRecord or type(record.outcome) is not ObservedExecution:
            raise TypeError("assertion preparation requires an observed ExecutionRecord")
        rules = self._rules(record.work.subject).assertions
        calls: list[AssertionCall] = []
        for rule in rules:
            expressions: list[PreparedAssertionExpression] = []
            for index, expression in enumerate(required_assertion_expressions(rule)):
                expressions.append(
                    PreparedAssertionExpression(
                        expression=expression,
                        result=await _assertion_operand(
                            self._context,
                            expression,
                            ResolutionTarget(
                                symbol=_operand_symbol("assertions", rule.effective_id, index),
                                scope=record.work.scope,
                                cell_inputs=cell_inputs(prepared, record.work.trial.cell),
                            ),
                        ),
                    )
                )
            application = AssertionApplication(
                application_id=RuleApplicationId(
                    value=uuid5(
                        record.work.scope.execution_id.value,
                        _application_name("assertion", rule.effective_id, rule.fingerprint),
                    )
                ),
                definition=rule.definition,
                effective_id=rule.effective_id,
                rule_fingerprint=rule.fingerprint,
                environment=record.work.environment,
                environment_execution_id=record.work.scope.execution_id,
            )
            calls.append(
                AssertionCall(
                    application=application,
                    rule=rule,
                    inputs=PreparedAssertionInputs(
                        rule_fingerprint=rule.fingerprint,
                        environment_execution_id=record.work.scope.execution_id,
                        expressions=tuple(expressions),
                    ),
                    control=_assertion_control(prepared, self._context, self._limits),
                )
            )
        return tuple(calls)

    async def diffs(
        self,
        prepared: PreparedRun,
        work: ComparisonWork,
        baseline: ExecutionRecord,
        candidate: ExecutionRecord,
    ) -> tuple[DiffCall, ...]:
        """Resolve side-scoped operands and bind real directed diff applications."""
        self._require_prepared(prepared)
        if type(work) is not ComparisonWork:
            raise TypeError("diff preparation requires ComparisonWork")
        if type(baseline) is not ExecutionRecord or type(candidate) is not ExecutionRecord:
            raise TypeError("diff preparation requires ExecutionRecord sides")
        if (
            type(baseline.outcome) is not ObservedExecution
            or type(candidate.outcome) is not ObservedExecution
        ):
            raise TypeError("diff preparation requires observed sides")
        rules = self._rules(work.subject).diffs
        calls: list[DiffCall] = []
        for rule in rules:
            required = required_diff_expressions(rule)
            baseline_expressions = await _diff_operands(
                self._context,
                required.baseline,
                "baseline",
                rule.effective_id,
                baseline,
                prepared,
            )
            candidate_expressions = await _diff_operands(
                self._context,
                required.candidate,
                "candidate",
                rule.effective_id,
                candidate,
                prepared,
            )
            shared_expressions = await _diff_operands(
                self._context,
                required.shared,
                "shared",
                rule.effective_id,
                baseline,
                prepared,
            )
            pair = DirectedEnvironmentPair(
                comparison_id=work.comparison_id,
                baseline_environment=work.pair.baseline,
                baseline_execution_id=work.baseline_execution_id,
                candidate_environment=work.pair.candidate,
                candidate_execution_id=work.candidate_execution_id,
            )
            application = DiffApplication(
                application_id=RuleApplicationId(
                    value=uuid5(
                        work.comparison_id.value,
                        _application_name("diff", rule.effective_id, rule.fingerprint),
                    )
                ),
                definition=rule.definition,
                effective_id=rule.effective_id,
                rule_fingerprint=rule.fingerprint,
                pair=pair,
            )
            calls.append(
                DiffCall(
                    application=application,
                    rule=rule,
                    inputs=PreparedDiffInputs(
                        rule_fingerprint=rule.fingerprint,
                        comparison_id=work.comparison_id,
                        baseline_execution_id=work.baseline_execution_id,
                        candidate_execution_id=work.candidate_execution_id,
                        baseline_expressions=baseline_expressions,
                        candidate_expressions=candidate_expressions,
                        shared_expressions=shared_expressions,
                    ),
                    control=_diff_control(prepared, work, self._context, self._limits),
                )
            )
        return tuple(calls)

    def _require_prepared(self, prepared: PreparedRun) -> None:
        if type(prepared) is not PreparedRun or prepared is not self._prepared:
            raise ValueError("evaluation preparation belongs to another PreparedRun")

    def _rules(self, subject: DefinitionRef) -> _SubjectRules:
        found = next((item for item in self._subjects if item.subject == subject), None)
        if found is None:
            raise ValueError("evaluation subject was not preflighted")
        return found


def bind_configuration_evaluation(
    prepared: PreparedRun,
    context: RunContext,
    limits: EvaluationRuleLimits,
) -> EvaluationBindingResult:
    """Lower every requested profile or reject the whole run before effects begin."""
    if type(prepared) is not PreparedRun or type(context) is not RunContext:
        raise TypeError("evaluation binding requires PreparedRun and RunContext")
    if type(limits) is not EvaluationRuleLimits:
        raise TypeError("evaluation binding requires EvaluationRuleLimits")
    problems = _Problems()
    subject_rules = tuple(
        _compile_subject(prepared.project, subject, problems)
        for subject in prepared.effective.subjects
    )
    gate = _compile_gate(prepared, subject_rules, problems)
    if problems.diagnostics:
        return EvaluationBindingResult(
            diagnostics=tuple(problems.diagnostics),
            rejected=tuple(problems.rejected),
        )
    return EvaluationBindingResult(
        preparation=ConfigurationEvaluationPreparation(
            prepared=prepared,
            context=context,
            limits=limits,
            subjects=subject_rules,
            gate=gate,
        )
    )


def _compile_subject(
    project: CompiledProject,
    subject: EffectiveSubject,
    problems: _Problems,
) -> _SubjectRules:
    assertions = tuple(
        rule
        for handle in subject.profiles.assertions
        for rule in _compile_assertion_profile(_profile(project, handle, problems), problems)
    )
    diffs = tuple(
        rule
        for handle in subject.profiles.diffs
        for rule in _compile_diff_profile(_profile(project, handle, problems), problems)
    )
    _require_unique_rules(assertions, problems, project, subject.profiles.assertions)
    _require_unique_rules(diffs, problems, project, subject.profiles.diffs)
    return _SubjectRules(subject=subject.definition, assertions=assertions, diffs=diffs)


def _profile(
    project: CompiledProject,
    handle: DefinitionHandle,
    problems: _Problems,
) -> CompiledResource | InlineResource | None:
    if type(handle) is DefinitionRef:
        found = project.find(handle.identity.kind, handle.identity.resource_id)
        if found is not None and found.definition == handle:
            return found
        problems.add(_PROFILE_MISSING, project.project)
        return None
    found_inline = next(
        (item for item in _inline_resources(project) if item.definition == handle),
        None,
    )
    if found_inline is not None:
        return found_inline
    problems.add(_PROFILE_MISSING, project.project)
    return None


def _inline_resources(project: CompiledProject) -> Iterable[InlineResource]:
    for resource in (project.project, *project.resources):
        yield from _walk_inline(resource.spec)


def _walk_inline(value: CanonicalValue) -> Iterable[InlineResource]:
    if type(value) is InlineResource:
        yield value
        yield from _walk_inline(value.spec)
    elif type(value) is CanonicalArray:
        for item in value.items:
            yield from _walk_inline(item)
    elif type(value) is CanonicalObject:
        for member in value.members:
            yield from _walk_inline(member.value)


def _compile_assertion_profile(
    profile: CompiledResource | InlineResource | None,
    problems: _Problems,
) -> tuple[CompiledAssertionRule, ...]:
    if profile is None:
        return ()
    if not _profile_fields_supported(profile, problems):
        return ()
    rules = object_member(_spec(profile), "rules")
    if type(rules) is not ArrayExpr or not rules.items:
        problems.add(_PROFILE_EMPTY, profile, field="rules")
        return ()
    result: list[CompiledAssertionRule] = []
    for index, expression in enumerate(rules.items):
        rule = _compile_assertion_rule(profile, expression, index, problems)
        if rule is not None:
            result.append(rule)
    return tuple(result)


def _compile_assertion_rule(
    profile: CompiledResource | InlineResource,
    expression: ValueExpression,
    index: int,
    problems: _Problems,
) -> CompiledAssertionRule | None:
    if type(expression) is not ObjectExpr:
        problems.add(_RULE_SHAPE, profile, rule_index=index)
        return None
    fields = _expression_fields(expression)
    if not _fields_supported(fields, _ASSERTION_FIELDS, profile, index, problems):
        return None
    rule_id = _rule_id(fields.get("id"), index, profile, problems)
    selector = _selector_value(fields.get("selector"), profile, index, problems)
    operator = _operation(fields.get("operator"))
    if operator is None or operator[0] not in _SUPPORTED_ASSERTIONS:
        alias = None if operator is None else operator[0]
        problems.add(
            _OPERATOR,
            profile,
            rule_index=index,
            field="operator",
            family=EvaluationCapabilityFamily.ASSERTION_OPERATOR,
            alias=_safe_alias(alias),
        )
        return None
    operation_name, operation_fields = operator
    if set(operation_fields) != {"name", "expected"}:
        problems.add(
            _OPERATOR,
            profile,
            rule_index=index,
            field="operator",
            family=EvaluationCapabilityFamily.ASSERTION_OPERATOR,
            alias=operation_name,
        )
        return None
    expected = operation_fields["expected"]
    if rule_id is None or selector is None:
        return None
    relation = (
        EqualityRelation.EQUALS if operation_name == "equals" else EqualityRelation.NOT_EQUALS
    )
    try:
        compiled = CompiledAssertionRule(
            definition=AssertionRuleDefinition(origin=_rule_origin(profile), rule_id=rule_id),
            effective_id=EffectiveRuleId(value=rule_id.value),
            fingerprint=_rule_fingerprint(profile, "assertion", rule_id, index),
            metadata=Metadata(),
            severity=_enum_field(fields.get("severity"), Severity, Severity.BLOCKING),
            selector=selector,
            operator_id=AssertionOperatorIdentity(
                namespace="comparo.core",
                kind=AssertionOperatorKind.EQUALITY,
                version="1",
            ),
            operator=EqualityOperator(relation=relation, expected=expected),
            quantifier=_enum_field(fields.get("quantifier"), Quantifier, Quantifier.ONE),
            completeness=_enum_field(
                fields.get("completeness"),
                CompletenessRequirement,
                CompletenessRequirement.COMPLETE,
            ),
        )
    except (TypeError, ValueError):
        problems.add(_RULE_SHAPE, profile, rule_index=index)
        return None
    return compiled


def _compile_diff_profile(
    profile: CompiledResource | InlineResource | None,
    problems: _Problems,
) -> tuple[CompiledDiffRule, ...]:
    if profile is None:
        return ()
    if not _profile_fields_supported(profile, problems):
        return ()
    rules = object_member(_spec(profile), "rules")
    if type(rules) is not ArrayExpr or not rules.items:
        problems.add(_PROFILE_EMPTY, profile, field="rules")
        return ()
    result: list[CompiledDiffRule] = []
    for index, expression in enumerate(rules.items):
        rule = _compile_diff_rule(profile, expression, index, problems)
        if rule is not None:
            result.append(rule)
    return tuple(result)


def _compile_diff_rule(
    profile: CompiledResource | InlineResource,
    expression: ValueExpression,
    index: int,
    problems: _Problems,
) -> CompiledDiffRule | None:
    if type(expression) is not ObjectExpr:
        problems.add(_RULE_SHAPE, profile, rule_index=index)
        return None
    fields = _expression_fields(expression)
    if not _fields_supported(fields, _DIFF_FIELDS, profile, index, problems):
        _reject_diff_operations(fields, profile, index, problems)
        return None
    rule_id = _rule_id(fields.get("id"), index, profile, problems)
    selector = _selector_value(fields.get("selector"), profile, index, problems)
    comparator = _operation(fields.get("comparator"))
    if comparator is None or comparator[0] not in _SUPPORTED_COMPARATORS:
        alias = None if comparator is None else comparator[0]
        problems.add(
            _OPERATOR,
            profile,
            rule_index=index,
            field="comparator",
            family=EvaluationCapabilityFamily.DIFF_COMPARATOR,
            alias=_safe_alias(alias),
        )
        return None
    comparator_name, comparator_fields = comparator
    if set(comparator_fields) != {"name"}:
        problems.add(
            _OPERATOR,
            profile,
            rule_index=index,
            field="comparator",
            family=EvaluationCapabilityFamily.DIFF_COMPARATOR,
            alias=comparator_name,
        )
        return None
    if rule_id is None or selector is None:
        return None
    pairing_identity = _diff_identity(
        DiffOperationFamily.PAIRING,
        "ordered"
        if selector.output.cardinality is SelectorCardinality.ZERO_OR_MORE
        else "singular",
    )
    pairing = (
        OrderedPairing(identity=pairing_identity)
        if selector.output.cardinality is SelectorCardinality.ZERO_OR_MORE
        else SingularPairing(identity=pairing_identity)
    )
    comparator_identity = _diff_identity(DiffOperationFamily.COMPARATOR, comparator_name)
    strategy = (
        ExactComparator(identity=comparator_identity)
        if comparator_name == "exact"
        else IgnoreComparator(identity=comparator_identity)
    )
    side = SidePipeline(selector=selector)
    try:
        return CompiledDiffRule(
            definition=DiffRuleDefinition(origin=_rule_origin(profile), rule_id=rule_id),
            effective_id=EffectiveRuleId(value=rule_id.value),
            fingerprint=_rule_fingerprint(profile, "diff", rule_id, index),
            metadata=Metadata(),
            severity=_enum_field(fields.get("severity"), DiffSeverity, DiffSeverity.BLOCKING),
            completeness=_enum_field(
                fields.get("completeness"),
                DiffCompletenessRequirement,
                DiffCompletenessRequirement.COMPLETE,
            ),
            pipeline=DiffPipeline(
                baseline=side,
                candidate=side,
                pairing=pairing,
                comparator=strategy,
            ),
        )
    except (TypeError, ValueError):
        problems.add(_RULE_SHAPE, profile, rule_index=index)
        return None


def _compile_gate(
    prepared: PreparedRun,
    subjects: tuple[_SubjectRules, ...],
    problems: _Problems,
) -> CompiledGateProfile | None:
    handles = tuple(
        dict.fromkeys(
            subject.profiles.gate
            for subject in prepared.effective.subjects
            if subject.profiles.gate is not None
        )
    )
    if not handles:
        return None
    if len(handles) != 1:
        problems.add(
            _GATE_MULTIPLE, prepared.project.project, family=EvaluationCapabilityFamily.GATE
        )
        return None
    handle = handles[0]
    profile = _profile(prepared.project, handle, problems)
    if profile is None:
        return None
    if type(profile) is InlineResource:
        problems.add(_INLINE_GATE, profile, family=EvaluationCapabilityFamily.GATE)
        return None
    if _spec(profile).members:
        problems.add(_GATE, profile, family=EvaluationCapabilityFamily.GATE)
        return None
    zero = Count(value=0)
    return CompiledGateProfile(
        definition=profile.definition,
        severities=(GateSeverity.BLOCKING,),
        environment_roles=(
            EnvironmentRole.BASELINE,
            EnvironmentRole.CANDIDATE,
            EnvironmentRole.OTHER,
        ),
        require_complete_run=True,
        require_assertion_coverage=any(item.assertions for item in subjects),
        require_diff_coverage=any(item.diffs for item in subjects),
        thresholds=(
            GateThreshold(
                severity=GateSeverity.BLOCKING,
                violated_rules=zero,
                drifted_rules=zero,
                unavailable_rules=zero,
                incomparable_rules=zero,
                incomplete_rules=zero,
                error_rules=zero,
            ),
        ),
    )


def _profile_fields_supported(
    profile: CompiledResource | InlineResource,
    problems: _Problems,
) -> bool:
    unsupported = [
        member.name for member in _spec(profile).members if member.name not in _PROFILE_FIELDS
    ]
    for field in unsupported:
        problems.add(
            _PROFILE_FIELD,
            profile,
            field=field,
            family=EvaluationCapabilityFamily.PROFILE_COMPOSITION,
        )
    return not unsupported


def _fields_supported(
    fields: dict[str, ValueExpression],
    supported: frozenset[str],
    profile: CompiledResource | InlineResource,
    index: int,
    problems: _Problems,
) -> bool:
    unsupported = [field for field in fields if field not in supported]
    for field in unsupported:
        problems.add(_RULE_FIELD, profile, rule_index=index, field=field)
    return not unsupported


def _reject_diff_operations(
    fields: dict[str, ValueExpression],
    profile: CompiledResource | InlineResource,
    index: int,
    problems: _Problems,
) -> None:
    families = {
        "transforms": EvaluationCapabilityFamily.DIFF_TRANSFORM,
        "reducer": EvaluationCapabilityFamily.DIFF_REDUCER,
        "pairing": EvaluationCapabilityFamily.DIFF_PAIRING,
    }
    for field, family in families.items():
        value = fields.get(field)
        if value is None:
            continue
        operations = (
            tuple(_operation(item) for item in value.items)
            if type(value) is ArrayExpr
            else (_operation(value),)
        )
        aliases = tuple(operation[0] for operation in operations if operation is not None)
        for alias in aliases or (None,):
            problems.rejected.append(
                RejectedEvaluationCapability(
                    family=family,
                    alias=_safe_alias(alias),
                    resource=_profile_context(profile)[0],
                    rule_index=Count(value=index),
                )
            )


def _selector_value(
    expression: ValueExpression | None,
    profile: CompiledResource | InlineResource,
    index: int,
    problems: _Problems,
) -> CompiledSelector | None:
    text = _literal_text(expression)
    if text is None:
        problems.add(
            _SELECTOR,
            profile,
            rule_index=index,
            field="selector",
            family=EvaluationCapabilityFamily.SELECTOR,
        )
        return None
    source: SelectorSource
    path = NoPath()
    view: BodyViewKind | None = None
    kinds: tuple[ValueKind, ...]
    if text in {"status", "response.status"}:
        source = SelectorSource.RESPONSE_STATUS
        kinds = (ValueKind.INTEGER,)
    elif text in {"body", "response.body"}:
        source = SelectorSource.RESPONSE_BODY
        view = BodyViewKind.JSON
        kinds = _JSON_KINDS
    else:
        problems.add(
            _SELECTOR,
            profile,
            rule_index=index,
            field="selector",
            family=EvaluationCapabilityFamily.SELECTOR,
        )
        return None
    capabilities = derive_selector_capabilities(source, path)
    return CompiledSelector(
        fingerprint=_selector_fingerprint(profile, text),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(kinds=kinds, cardinality=capabilities.cardinality),
        body_view=view,
    )


def _operation(
    expression: ValueExpression | None,
) -> tuple[str, dict[str, ValueExpression]] | None:
    shorthand = _literal_text(expression)
    if shorthand is not None and expression is not None:
        return _normalize_operation(shorthand), {"name": expression}
    if type(expression) is not ObjectExpr:
        return None
    fields = _expression_fields(expression)
    name = _literal_text(fields.get("name"))
    if name is None:
        return None
    return _normalize_operation(name), fields


def _normalize_operation(name: str) -> str:
    prefix = "comparo.core/"
    return name[len(prefix) : -2] if name.startswith(prefix) and name.endswith("@1") else name


def _safe_alias(alias: str | None) -> str | None:
    if alias is None:
        return None
    normalized = _normalize_operation(alias)
    catalogs = (
        _SUPPORTED_ASSERTIONS
        | _SUPPORTED_COMPARATORS
        | {
            "approximatelyEquals",
            "between",
            "cardinality",
            "conformsTo",
            "contains",
            "containsAll",
            "containsAny",
            "containsToken",
            "decodesAs",
            "digestEquals",
            "endsWith",
            "greater",
            "greaterOrEqual",
            "hasKey",
            "hasKeys",
            "hasOnlyKeys",
            "in",
            "isType",
            "length",
            "less",
            "lessOrEqual",
            "matches",
            "mediaTypeIs",
            "mediaTypeMatches",
            "notContains",
            "notIn",
            "notMatches",
            "startsWith",
            "collectionFilter",
            "collectionMap",
            "digest",
            "exclude",
            "jsonShape",
            "normalizeText",
            "project",
            "relativeSelect",
            "sortBy",
            "streamProject",
            "valueType",
            "collect",
            "concatenate",
            "count",
            "distinct",
            "first",
            "groupBy",
            "last",
            "maximum",
            "minimum",
            "multiset",
            "objectFromEntries",
            "set",
            "sum",
            "keyed",
            "ordered",
            "singular",
            "numericTolerance",
            "subset",
            "superset",
            "textPredicate",
            "type",
        }
    )
    return normalized if normalized in catalogs else None


def _expression_fields(value: ObjectExpr) -> dict[str, ValueExpression]:
    return {member.name: member.expression for member in value.members}


def _literal_text(value: ValueExpression | None) -> str | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonString
        else None
    )


def _enum_field[EnumT: DomainStrEnum](
    value: ValueExpression | None,
    enum_type: type[EnumT],
    default: EnumT,
) -> EnumT:
    text = _literal_text(value)
    return default if value is None else enum_type(text) if text is not None else enum_type("")


def _rule_id(
    value: ValueExpression | None,
    index: int,
    profile: CompiledResource | InlineResource,
    problems: _Problems,
) -> RuleId | None:
    text = f"rule-{index + 1}" if value is None else _literal_text(value)
    if text is None:
        problems.add(_RULE_ID, profile, rule_index=index, field="id")
        return None
    try:
        return RuleId(value=text)
    except ValueError:
        problems.add(_RULE_ID, profile, rule_index=index, field="id")
        return None


def _require_unique_rules(
    rules: tuple[CompiledAssertionRule, ...] | tuple[CompiledDiffRule, ...],
    problems: _Problems,
    project: CompiledProject,
    handles: tuple[DefinitionHandle, ...],
) -> None:
    ids = tuple(rule.effective_id for rule in rules)
    if len(ids) == len(set(ids)):
        return
    profile = _profile(project, handles[-1], problems) if handles else project.project
    if profile is not None:
        problems.add(_RULE_DUPLICATE, profile, field="rules")


def _spec(profile: CompiledResource | InlineResource) -> CanonicalObject:
    if type(profile) is InlineResource:
        return profile.spec
    if type(profile.spec) is not CanonicalObject:
        raise TypeError("profile spec must be a CanonicalObject")
    return profile.spec


def _rule_origin(
    profile: CompiledResource | InlineResource,
) -> DefinitionRef | InlineDefinitionOrigin:
    return profile.definition if type(profile) is CompiledResource else profile.origin


def _profile_context(
    profile: CompiledResource | InlineResource,
) -> tuple[ResourceIdentity, SourceLocation | None]:
    if type(profile) is CompiledResource:
        return profile.definition.identity, profile.location
    return profile.origin.owner, None


def _fingerprint(*parts: str) -> Fingerprint:
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=digest.hexdigest()),
    )


def _profile_fingerprint(profile: CompiledResource | InlineResource) -> Fingerprint:
    return profile.definition.fingerprint


def _rule_fingerprint(
    profile: CompiledResource | InlineResource,
    family: str,
    rule_id: RuleId,
    index: int,
) -> Fingerprint:
    return _fingerprint(
        "comparo.evaluation-rule.v1",
        _profile_fingerprint(profile).digest.hex_value,
        family,
        rule_id.value,
        str(index),
    )


def _selector_fingerprint(profile: CompiledResource | InlineResource, selector: str) -> Fingerprint:
    return _fingerprint(
        "comparo.selector.v1",
        _profile_fingerprint(profile).digest.hex_value,
        selector,
    )


def _diff_identity(family: DiffOperationFamily, name: str) -> DiffOperationIdentity:
    return DiffOperationIdentity(
        namespace="comparo.core",
        family=family,
        name=name,
        version="1",
    )


def _application_name(family: str, effective_id: EffectiveRuleId, fingerprint: Fingerprint) -> str:
    return f"{family}:{effective_id.value}:{fingerprint.digest.hex_value}"


def _operand_symbol(family: str, effective_id: EffectiveRuleId, index: int) -> SymbolPath:
    return SymbolPath(segments=(family, effective_id.value, f"operand-{index + 1}"))


async def _assertion_operand(
    context: RunContext,
    expression: ValueExpression,
    target: ResolutionTarget,
) -> AssertionOperandReady | AssertionOperandUnavailable | AssertionOperandError:
    result = await context.resolve(expression, target)
    if type(result) is Present:
        return AssertionOperandReady(value=result.value)
    if type(result) in {Unavailable, Missing}:
        return AssertionOperandUnavailable(code=_RESOLUTION_UNAVAILABLE)
    if type(result) in {Error, Blocked}:
        return AssertionOperandError(code=_RESOLUTION_ERROR)
    raise TypeError("resolution returned an unsupported outcome")


async def _diff_operands(
    context: RunContext,
    expressions: tuple[ValueExpression, ...],
    side: str,
    effective_id: EffectiveRuleId,
    record: ExecutionRecord,
    prepared: PreparedRun,
) -> tuple[PreparedDiffExpression, ...]:
    result: list[PreparedDiffExpression] = []
    for index, expression in enumerate(expressions):
        resolved = await context.resolve(
            expression,
            ResolutionTarget(
                symbol=_operand_symbol(f"diffs-{side}", effective_id, index),
                scope=record.work.scope,
                cell_inputs=cell_inputs(prepared, record.work.trial.cell),
            ),
        )
        operand: DiffOperandReady | DiffOperandUnavailable | DiffOperandError
        if type(resolved) is Present:
            operand = DiffOperandReady(value=resolved.value)
        elif type(resolved) in {Unavailable, Missing}:
            operand = DiffOperandUnavailable(code=_RESOLUTION_UNAVAILABLE)
        elif type(resolved) in {Error, Blocked}:
            operand = DiffOperandError(code=_RESOLUTION_ERROR)
        else:
            raise TypeError("resolution returned an unsupported outcome")
        result.append(PreparedDiffExpression(expression=expression, result=operand))
    return tuple(result)


def _assertion_control(
    prepared: PreparedRun,
    context: RunContext,
    limits: EvaluationRuleLimits,
) -> AssertionControl:
    operations = assertion_budget_operations()
    return AssertionControl(
        selection=_selection_control(prepared, context, limits),
        budgets=AssertionBudgets(
            work=_budget(
                prepared,
                BudgetResource.TRANSFORM_WORK,
                BudgetStage.EVALUATION,
                operations[0],
                limits.transform_work,
            ),
            facts=_budget(
                prepared, BudgetResource.FACTS, BudgetStage.EVALUATION, operations[1], limits.facts
            ),
            evidence_examples=_budget(
                prepared,
                BudgetResource.EVIDENCE_EXAMPLES,
                BudgetStage.EVIDENCE,
                operations[2],
                limits.evidence_examples,
            ),
            evidence_bytes=_budget(
                prepared,
                BudgetResource.EVIDENCE_BYTES,
                BudgetStage.EVIDENCE,
                operations[3],
                limits.evidence_bytes,
                bytes_unit=True,
            ),
        ),
    )


def _diff_control(
    prepared: PreparedRun,
    work: ComparisonWork,
    context: RunContext,
    limits: EvaluationRuleLimits,
) -> DiffControl:
    operations = diff_budget_operations()
    scope = ComparisonBudgetScope(run=context.run, comparison_id=work.comparison_id)
    return DiffControl(
        baseline_selection=_selection_control(prepared, context, limits, scope=scope),
        candidate_selection=_selection_control(prepared, context, limits, scope=scope),
        budgets=DiffBudgets(
            derived_values=_budget(
                prepared,
                BudgetResource.DERIVED_VALUES,
                BudgetStage.EVALUATION,
                operations[0],
                limits.derived_values,
                scope=scope,
            ),
            derived_bytes=_budget(
                prepared,
                BudgetResource.DERIVED_BYTES,
                BudgetStage.EVALUATION,
                operations[1],
                limits.derived_bytes,
                bytes_unit=True,
                scope=scope,
            ),
            transform_work=_budget(
                prepared,
                BudgetResource.TRANSFORM_WORK,
                BudgetStage.EVALUATION,
                operations[2],
                limits.transform_work,
                scope=scope,
            ),
            pairs=_budget(
                prepared,
                BudgetResource.PAIRS,
                BudgetStage.EVALUATION,
                operations[3],
                limits.pairs,
                scope=scope,
            ),
            facts=_budget(
                prepared,
                BudgetResource.FACTS,
                BudgetStage.EVALUATION,
                operations[4],
                limits.facts,
                scope=scope,
            ),
            evidence_examples=_budget(
                prepared,
                BudgetResource.EVIDENCE_EXAMPLES,
                BudgetStage.EVIDENCE,
                operations[5],
                limits.evidence_examples,
                scope=scope,
            ),
            evidence_bytes=_budget(
                prepared,
                BudgetResource.EVIDENCE_BYTES,
                BudgetStage.EVIDENCE,
                operations[6],
                limits.evidence_bytes,
                bytes_unit=True,
                scope=scope,
            ),
        ),
        cancellation=DiffCancellation(cancelled=context.cancellation.cancelled, code=_CANCELLED),
    )


def _selection_control(
    prepared: PreparedRun,
    context: RunContext,
    limits: EvaluationRuleLimits,
    *,
    scope: ComparisonBudgetScope | None = None,
) -> SelectionControl:
    operations = selection_budget_operations()
    return SelectionControl(
        budgets=SelectionBudgets(
            work=_budget(
                prepared,
                BudgetResource.SELECTION_WORK,
                BudgetStage.SELECTION,
                operations[0],
                limits.selection_work,
                scope=scope,
            ),
            matches=_budget(
                prepared,
                BudgetResource.SELECTION_MATCHES,
                BudgetStage.SELECTION,
                operations[1],
                limits.selection_matches,
                scope=scope,
            ),
            result_bytes=_budget(
                prepared,
                BudgetResource.SELECTED_RESULT_BYTES,
                BudgetStage.SELECTION,
                operations[2],
                limits.selected_result_bytes,
                bytes_unit=True,
                scope=scope,
            ),
        ),
        cancellation=SelectionCancellation(
            cancelled=context.cancellation.cancelled,
            code=_CANCELLED,
        ),
    )


def _budget(
    prepared: PreparedRun,
    resource: BudgetResource,
    stage: BudgetStage,
    operation: str,
    limit: int,
    *,
    bytes_unit: bool = False,
    scope: ComparisonBudgetScope | None = None,
) -> ResourceBudget:
    amount = ByteCount(value=limit) if bytes_unit else Count(value=limit)
    zero = type(amount)(value=0)
    budget_scope = scope if scope is not None else RunScope(run_id=prepared.plan.run_id)
    return ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.COMPARISON if scope is not None else BudgetScopeKind.RUN,
        stage=stage,
        operation=BudgetOperation(value=operation),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=amount,
                origin=DefaultBudgetOrigin(
                    default_set=VersionedDefaultSetRef(
                        identifier="comparo.evaluation-rule-limits",
                        version="1",
                        fingerprint=_fingerprint(
                            "comparo.evaluation-rule-limits.v1",
                            resource.value,
                            str(limit),
                        ),
                    )
                ),
            ),
            BudgetContribution(
                kind=BudgetContributionKind.REQUEST,
                amount=amount,
                origin=InvocationBudgetOrigin(invocation_id=prepared.invocation.invocation_id),
            ),
        ),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=budget_scope,
    )


__all__ = (
    "ConfigurationEvaluationPreparation",
    "EvaluationBindingResult",
    "EvaluationCapabilityFamily",
    "EvaluationRuleLimits",
    "RejectedEvaluationCapability",
    "bind_configuration_evaluation",
)
