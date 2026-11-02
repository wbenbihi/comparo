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

from comparo.application.digest_validation import DEFAULT_DIGEST_VALIDATION_LIMITS_V1
from comparo.application.digest_validation import DigestValidationLimits
from comparo.application.digest_validation import prepare_digest_predicate
from comparo.application.evaluation_assertions import InvalidAssertionOperationError
from comparo.application.evaluation_assertions import compile_assertion_operation
from comparo.application.evaluation_diffs import DiffSyntaxFamily
from comparo.application.evaluation_diffs import InvalidDiffOperationError
from comparo.application.evaluation_diffs import compile_comparator
from comparo.application.evaluation_diffs import compile_pairing
from comparo.application.evaluation_diffs import compile_reducer
from comparo.application.evaluation_diffs import compile_transforms
from comparo.application.evaluation_selectors import InvalidSelectorError
from comparo.application.evaluation_selectors import compile_selector
from comparo.application.regex_validation import DEFAULT_REGEX_VALIDATION_LIMITS_V1
from comparo.application.regex_validation import PreparedRegex
from comparo.application.regex_validation import PreparedRegexCatalog
from comparo.application.regex_validation import RegexValidationLimits
from comparo.application.regex_validation import prepare_compiled_regex
from comparo.application.remote_schemas import remote_schema_allowed
from comparo.application.schema_validation import DEFAULT_SCHEMA_VALIDATION_LIMITS_V1
from comparo.application.schema_validation import PreparedSchema
from comparo.application.schema_validation import PreparedSchemaCatalog
from comparo.application.schema_validation import RemoteSchemaDocuments
from comparo.application.schema_validation import SchemaValidationLimits
from comparo.application.schema_validation import prepare_local_schema
from comparo.application.schema_validation import prepare_remote_schema
from comparo.application.schema_validation import schema_operator
from comparo.core.assertions.accounting import AssertionBudgets
from comparo.core.assertions.accounting import assertion_budget_operations
from comparo.core.assertions.engine import AssertionControl
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.inputs import PreparedExpression as PreparedAssertionExpression
from comparo.core.assertions.inputs import PreparedOperandError as AssertionOperandError
from comparo.core.assertions.inputs import PreparedOperandReady as AssertionOperandReady
from comparo.core.assertions.inputs import PreparedOperandUnavailable as AssertionOperandUnavailable
from comparo.core.assertions.inputs import PreparedPredicate
from comparo.core.assertions.inputs import required_expressions as required_assertion_expressions
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.configuration.v1alpha1.operations import ASSERTION_OPERATION_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_COMPARATOR_ALIASES
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
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import operator_uses_match_predicate
from comparo.core.domain.assertions.regex import RegexOperator
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
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import DiffRuleDefinition
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.rules import DirectedEnvironmentPair
from comparo.core.domain.diffs.rules import SidePipeline
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
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
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
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.selector_semantics import derive_selector_output_contract
from comparo.core.domain.selectors.sources import STREAM_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import Unavailable
from comparo.core.planning._canonical import object_member
from comparo.core.planning.models import DefinitionHandle
from comparo.core.planning.models import EffectiveSubject
from comparo.core.planning.models import PreparedRun
from comparo.core.policy import CompiledExecutionPolicy
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
from comparo.core.selection import SelectionSnapshotRequirements
from comparo.core.selection import select
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
_SCHEMA = DiagnosticCode(value="evaluation.schema.invalid")
_GATE = DiagnosticCode(value="evaluation.gate.unsupported")
_GATE_MULTIPLE = DiagnosticCode(value="evaluation.gate.multiple")
_INLINE_GATE = DiagnosticCode(value="evaluation.gate.inline-unsupported")
_RESOLUTION_UNAVAILABLE = DiagnosticCode(value="evaluation.operand.unavailable")
_RESOLUTION_ERROR = DiagnosticCode(value="evaluation.operand.error")
_CANCELLED = DiagnosticCode(value="evaluation.cancelled")

_ASSERTION_FIELDS = frozenset(
    {
        "id",
        "name",
        "description",
        "tags",
        "selector",
        "operator",
        "severity",
        "quantifier",
        "completeness",
    }
)
_DIFF_FIELDS = frozenset(
    {
        "id",
        "name",
        "description",
        "tags",
        "selector",
        "baseline",
        "candidate",
        "transforms",
        "reducer",
        "pairing",
        "comparator",
        "severity",
        "completeness",
    }
)
_PROFILE_FIELDS = frozenset({"include", "rules"})


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
class EvaluationPreflightResult(msgspec.Struct, frozen=True, kw_only=True):
    """Effect-free exact rule and gate compilation result for Plan admission."""

    diagnostics: tuple[Diagnostic, ...]
    rejected: tuple[RejectedEvaluationCapability, ...]
    assertion_rules: int
    diff_rules: int
    gate: CompiledGateProfile | None

    def __post_init__(self) -> None:
        """Keep successful counts/gate and rejected diagnostics structurally coherent."""
        if type(self.diagnostics) is not tuple or any(
            type(item) is not Diagnostic for item in self.diagnostics
        ):
            raise TypeError("evaluation preflight diagnostics must be immutable and typed")
        if type(self.rejected) is not tuple or any(
            type(item) is not RejectedEvaluationCapability for item in self.rejected
        ):
            raise TypeError("evaluation preflight rejections must be immutable and typed")
        if any(
            type(item) is not int or item < 0 for item in (self.assertion_rules, self.diff_rules)
        ):
            raise ValueError("evaluation preflight rule counts must be non-negative integers")
        if self.gate is not None and type(self.gate) is not CompiledGateProfile:
            raise TypeError("evaluation preflight gate must be compiled or absent")
        if self.diagnostics and (self.assertion_rules or self.diff_rules or self.gate is not None):
            raise ValueError("rejected evaluation preflight cannot retain compiled products")

    @property
    def supported(self) -> bool:
        """Return whether the selected validation configuration is execution-ready."""
        return not self.diagnostics


@final
class CompiledSubjectRules(msgspec.Struct, frozen=True, kw_only=True):
    """Exact compiled assertion and diff rules attached to one admitted subject."""

    subject: DefinitionRef
    assertions: tuple[CompiledAssertionRule, ...]
    diffs: tuple[CompiledDiffRule, ...]

    def __post_init__(self) -> None:
        """Require immutable typed rule collections and a subject definition."""
        if type(self.subject) is not DefinitionRef:
            raise TypeError("compiled evaluation subject must be a DefinitionRef")
        if type(self.assertions) is not tuple or any(
            type(item) is not CompiledAssertionRule for item in self.assertions
        ):
            raise TypeError("compiled subject assertions must be immutable and typed")
        if type(self.diffs) is not tuple or any(
            type(item) is not CompiledDiffRule for item in self.diffs
        ):
            raise TypeError("compiled subject diffs must be immutable and typed")


@final
class EvaluationRuleCatalogResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one compiled rule catalog or its value-free diagnostics."""

    subjects: tuple[CompiledSubjectRules, ...] = ()
    gate: CompiledGateProfile | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    rejected: tuple[RejectedEvaluationCapability, ...] = ()

    def __post_init__(self) -> None:
        """Keep usable catalogs and rejected compilation products disjoint."""
        if type(self.subjects) is not tuple or any(
            type(item) is not CompiledSubjectRules for item in self.subjects
        ):
            raise TypeError("evaluation rule catalog subjects must be immutable and typed")
        if self.gate is not None and type(self.gate) is not CompiledGateProfile:
            raise TypeError("evaluation rule catalog gate must be compiled or absent")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not Diagnostic for item in self.diagnostics
        ):
            raise TypeError("evaluation rule catalog diagnostics must be immutable and typed")
        if type(self.rejected) is not tuple or any(
            type(item) is not RejectedEvaluationCapability for item in self.rejected
        ):
            raise TypeError("evaluation rule catalog rejections must be immutable and typed")
        if self.diagnostics:
            if self.subjects or self.gate is not None:
                raise ValueError("rejected evaluation catalog cannot retain compiled products")
        elif not self.subjects:
            raise ValueError("successful evaluation catalog requires admitted subjects")


def _snapshot_requirements(
    subjects: tuple[CompiledSubjectRules, ...],
) -> SelectionSnapshotRequirements:
    """Derive adapter materialization needs solely from already-compiled selectors."""
    selectors = tuple(
        selector
        for subject in subjects
        for selector in (
            *(rule.selector for rule in subject.assertions),
            *(rule.pipeline.baseline.selector for rule in subject.diffs),
            *(rule.pipeline.candidate.selector for rule in subject.diffs),
        )
    )
    body_views = {
        selector.body_view
        for selector in selectors
        if selector.source in {SelectorSource.REQUEST_BODY, SelectorSource.RESPONSE_BODY}
        and selector.body_view not in {None, BodyViewKind.HANDLE}
    }
    stream_views = {
        BodyViewKind.SSE_EVENTS,
        BodyViewKind.NDJSON_RECORDS,
        BodyViewKind.JSON_SEQUENCE_RECORDS,
    }
    requires_stream = any(
        selector.source in STREAM_SELECTOR_SOURCES
        or selector.body_view is BodyViewKind.AUTO
        or selector.body_view in stream_views
        for selector in selectors
    )
    return SelectionSnapshotRequirements(
        body_views=tuple(sorted(body_views - stream_views, key=lambda item: item.value)),
        stream=requires_stream,
    )


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

    __slots__ = (
        "_context",
        "_digest_limits",
        "_gate",
        "_limits",
        "_prepared",
        "_regexes",
        "_schemas",
        "_snapshot_requirements",
        "_subjects",
    )

    def __init__(
        self,
        *,
        prepared: PreparedRun,
        context: RunContext,
        limits: EvaluationRuleLimits,
        subjects: tuple[CompiledSubjectRules, ...],
        gate: CompiledGateProfile | None,
        schemas: PreparedSchemaCatalog,
        regexes: PreparedRegexCatalog,
        digest_limits: DigestValidationLimits,
    ) -> None:
        """Bind one admitted run, its context, finite limits, rules, and optional gate."""
        if type(prepared) is not PreparedRun or type(context) is not RunContext:
            raise TypeError("configuration evaluation requires PreparedRun and RunContext")
        if type(limits) is not EvaluationRuleLimits:
            raise TypeError("configuration evaluation requires EvaluationRuleLimits")
        if type(subjects) is not tuple or any(
            type(item) is not CompiledSubjectRules for item in subjects
        ):
            raise TypeError("configuration evaluation subjects must be an immutable typed tuple")
        if gate is not None and type(gate) is not CompiledGateProfile:
            raise TypeError("configuration evaluation gate must be compiled")
        if type(schemas) is not PreparedSchemaCatalog:
            raise TypeError("configuration evaluation schemas must be prepared")
        if type(regexes) is not PreparedRegexCatalog:
            raise TypeError("configuration evaluation regexes must be prepared")
        if type(digest_limits) is not DigestValidationLimits:
            raise TypeError("configuration evaluation digest limits must be typed")
        if context.run.run_id != prepared.plan.run_id:
            raise ValueError("configuration evaluation context belongs to another run")
        self._prepared = prepared
        self._context = context
        self._limits = limits
        self._subjects = subjects
        self._gate = gate
        self._schemas = schemas
        self._regexes = regexes
        self._digest_limits = digest_limits
        self._snapshot_requirements = _snapshot_requirements(subjects)

    @property
    def gate_profile(self) -> CompiledGateProfile | None:
        """Return the single preflighted run gate, if requested."""
        return self._gate

    @property
    def snapshot_requirements(self) -> SelectionSnapshotRequirements:
        """Return the minimum body semantics required by the preflighted rules."""
        return self._snapshot_requirements

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
            control = _assertion_control(prepared, self._context, self._limits)
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
            predicates = _prepared_predicates(
                self._schemas,
                self._regexes,
                rule,
                record.outcome,
                control.selection,
                tuple(expressions),
                self._digest_limits,
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
                        predicates=predicates,
                    ),
                    control=control,
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

    def _rules(self, subject: DefinitionRef) -> CompiledSubjectRules:
        found = next((item for item in self._subjects if item.subject == subject), None)
        if found is None:
            raise ValueError("evaluation subject was not preflighted")
        return found


def bind_configuration_evaluation(
    prepared: PreparedRun,
    context: RunContext,
    limits: EvaluationRuleLimits,
    schema_limits: SchemaValidationLimits = DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
    regex_limits: RegexValidationLimits = DEFAULT_REGEX_VALIDATION_LIMITS_V1,
    digest_limits: DigestValidationLimits = DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
    *,
    policy: CompiledExecutionPolicy | None = None,
    remote_documents: RemoteSchemaDocuments | None = None,
) -> EvaluationBindingResult:
    """Lower every requested profile or reject the whole run before effects begin."""
    if type(prepared) is not PreparedRun or type(context) is not RunContext:
        raise TypeError("evaluation binding requires PreparedRun and RunContext")
    if type(limits) is not EvaluationRuleLimits:
        raise TypeError("evaluation binding requires EvaluationRuleLimits")
    if type(schema_limits) is not SchemaValidationLimits:
        raise TypeError("evaluation binding requires SchemaValidationLimits")
    if type(regex_limits) is not RegexValidationLimits:
        raise TypeError("evaluation binding requires RegexValidationLimits")
    if type(digest_limits) is not DigestValidationLimits:
        raise TypeError("evaluation binding requires DigestValidationLimits")
    if policy is not None and type(policy) is not CompiledExecutionPolicy:
        raise TypeError("evaluation binding policy must be compiled")
    if remote_documents is not None and type(remote_documents) is not RemoteSchemaDocuments:
        raise TypeError("evaluation binding remote schemas must be prepared documents")
    subject_rules, gate, schemas, regexes, problems = _compile_configuration(
        prepared,
        schema_limits,
        regex_limits,
        policy,
        remote_documents,
        False,
    )
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
            schemas=schemas,
            regexes=regexes,
            digest_limits=digest_limits,
        )
    )


def preflight_configuration_evaluation(
    prepared: PreparedRun,
    schema_limits: SchemaValidationLimits = DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
    regex_limits: RegexValidationLimits = DEFAULT_REGEX_VALIDATION_LIMITS_V1,
    *,
    policy: CompiledExecutionPolicy | None = None,
) -> EvaluationPreflightResult:
    """Compile all selected rules and the exact gate formula without acquiring effects."""
    if type(prepared) is not PreparedRun:
        raise TypeError("evaluation preflight requires PreparedRun")
    if type(schema_limits) is not SchemaValidationLimits:
        raise TypeError("evaluation preflight requires SchemaValidationLimits")
    if type(regex_limits) is not RegexValidationLimits:
        raise TypeError("evaluation preflight requires RegexValidationLimits")
    if policy is not None and type(policy) is not CompiledExecutionPolicy:
        raise TypeError("evaluation preflight policy must be compiled")
    subject_rules, gate, _schemas, _regexes, problems = _compile_configuration(
        prepared,
        schema_limits,
        regex_limits,
        policy,
        None,
        True,
    )
    if problems.diagnostics:
        return EvaluationPreflightResult(
            diagnostics=tuple(problems.diagnostics),
            rejected=tuple(problems.rejected),
            assertion_rules=0,
            diff_rules=0,
            gate=None,
        )
    return EvaluationPreflightResult(
        diagnostics=(),
        rejected=(),
        assertion_rules=sum(len(item.assertions) for item in subject_rules),
        diff_rules=sum(len(item.diffs) for item in subject_rules),
        gate=gate,
    )


def compile_configuration_rule_catalog(
    prepared: PreparedRun,
    schema_limits: SchemaValidationLimits = DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
    regex_limits: RegexValidationLimits = DEFAULT_REGEX_VALIDATION_LIMITS_V1,
    *,
    policy: CompiledExecutionPolicy | None = None,
) -> EvaluationRuleCatalogResult:
    """Compile the exact pre-run rule catalog without resolving values or causing effects."""
    if type(prepared) is not PreparedRun:
        raise TypeError("evaluation rule catalog requires PreparedRun")
    if type(schema_limits) is not SchemaValidationLimits:
        raise TypeError("evaluation rule catalog requires SchemaValidationLimits")
    if type(regex_limits) is not RegexValidationLimits:
        raise TypeError("evaluation rule catalog requires RegexValidationLimits")
    if policy is not None and type(policy) is not CompiledExecutionPolicy:
        raise TypeError("evaluation rule catalog policy must be compiled")
    subjects, gate, _schemas, _regexes, problems = _compile_configuration(
        prepared,
        schema_limits,
        regex_limits,
        policy,
        None,
        True,
    )
    if problems.diagnostics:
        return EvaluationRuleCatalogResult(
            diagnostics=tuple(problems.diagnostics),
            rejected=tuple(problems.rejected),
        )
    return EvaluationRuleCatalogResult(subjects=subjects, gate=gate)


def _compile_configuration(
    prepared: PreparedRun,
    schema_limits: SchemaValidationLimits,
    regex_limits: RegexValidationLimits,
    policy: CompiledExecutionPolicy | None,
    remote_documents: RemoteSchemaDocuments | None,
    defer_remote: bool,
) -> tuple[
    tuple[CompiledSubjectRules, ...],
    CompiledGateProfile | None,
    PreparedSchemaCatalog,
    PreparedRegexCatalog,
    _Problems,
]:
    problems = _Problems()
    schema_entries: list[PreparedSchema] = []
    subject_rules = tuple(
        _compile_subject(
            prepared.project,
            subject,
            schema_entries,
            schema_limits,
            policy,
            remote_documents,
            defer_remote,
            problems,
        )
        for subject in prepared.effective.subjects
    )
    gate = _compile_gate(prepared, subject_rules, problems)
    schemas = PreparedSchemaCatalog(
        entries=tuple(
            item
            for index, item in enumerate(schema_entries)
            if item.compiled not in tuple(previous.compiled for previous in schema_entries[:index])
        ),
        limits=schema_limits,
    )
    regex_entries: list[PreparedRegex] = []
    for subject in subject_rules:
        for rule in subject.assertions:
            if type(rule.operator) is not RegexOperator:
                continue
            try:
                prepared_regex = prepare_compiled_regex(rule.operator, regex_limits)
            except (TypeError, ValueError):
                problems.add(
                    _OPERATOR,
                    prepared.project.project,
                    family=EvaluationCapabilityFamily.ASSERTION_OPERATOR,
                    alias="matches",
                )
                continue
            if prepared_regex.operator not in tuple(item.operator for item in regex_entries):
                regex_entries.append(prepared_regex)
    regexes = PreparedRegexCatalog(entries=tuple(regex_entries), limits=regex_limits)
    return subject_rules, gate, schemas, regexes, problems


def _compile_subject(
    project: CompiledProject,
    subject: EffectiveSubject,
    schema_entries: list[PreparedSchema],
    schema_limits: SchemaValidationLimits,
    policy: CompiledExecutionPolicy | None,
    remote_documents: RemoteSchemaDocuments | None,
    defer_remote: bool,
    problems: _Problems,
) -> CompiledSubjectRules:
    profile_assertions = tuple(
        rule
        for handle in subject.profiles.assertions
        for rule in _compile_assertion_profile(_profile(project, handle, problems), problems)
    )
    schema_assertion = _compile_response_schema(
        project,
        subject,
        schema_entries,
        schema_limits,
        policy,
        remote_documents,
        defer_remote,
        problems,
    )
    assertions = (
        profile_assertions if schema_assertion is None else (*profile_assertions, schema_assertion)
    )
    diffs = tuple(
        rule
        for handle in subject.profiles.diffs
        for rule in _compile_diff_profile(_profile(project, handle, problems), problems)
    )
    _require_unique_rules(assertions, problems, project, subject.profiles.assertions)
    _require_unique_rules(diffs, problems, project, subject.profiles.diffs)
    return CompiledSubjectRules(
        subject=subject.definition,
        assertions=assertions,
        diffs=diffs,
    )


def _compile_response_schema(
    project: CompiledProject,
    subject: EffectiveSubject,
    entries: list[PreparedSchema],
    limits: SchemaValidationLimits,
    policy: CompiledExecutionPolicy | None,
    remote_documents: RemoteSchemaDocuments | None,
    defer_remote: bool,
    problems: _Problems,
) -> CompiledAssertionRule | None:
    """Lower a Request responseSchema into one ordinary implicit assertion rule."""
    if subject.definition.identity.kind is not ResourceKind.REQUEST:
        return None
    resource = project.find(
        subject.definition.identity.kind,
        subject.definition.identity.resource_id,
    )
    if resource is None or resource.definition != subject.definition:
        problems.add(_PROFILE_MISSING, project.project)
        return None
    binding = object_member(_spec(resource), "responseSchema")
    if binding is None:
        return None
    try:
        if type(binding) is RemoteSchema:
            if policy is None or not remote_schema_allowed(binding.uri, policy):
                raise ValueError("remote schema source is not authorized")
            payload = None if remote_documents is None else remote_documents.get(binding.uri)
            if payload is None and not defer_remote:
                raise ValueError("authorized remote schema document was not prepared")
            prepared = prepare_remote_schema(
                binding,
                b"{}" if payload is None else payload,
                limits,
            )
        else:
            prepared = prepare_local_schema(project, subject.definition, binding, limits)
        operator = schema_operator(prepared)
        entries.append(prepared)
        selector = _response_schema_selector(resource)
        rule_id = RuleId(value="comparo-response-schema")
        return CompiledAssertionRule(
            definition=AssertionRuleDefinition(
                origin=InlineDefinitionOrigin(
                    owner=subject.definition.identity,
                    fragment=InlineFragment(value="#response-schema-assertion"),
                ),
                rule_id=rule_id,
            ),
            effective_id=EffectiveRuleId(value=rule_id.value),
            fingerprint=_fingerprint(
                "comparo.response-schema-rule.v1",
                subject.definition.fingerprint.digest.hex_value,
                prepared.compiled.fingerprint.digest.hex_value,
            ),
            metadata=Metadata(
                name="Response schema",
                description="Validate the JSON response against the configured schema.",
                tags=("schema",),
            ),
            severity=Severity.BLOCKING,
            selector=selector,
            operator_id=AssertionOperatorIdentity(
                namespace="comparo.core",
                kind=AssertionOperatorKind.CONFORMS_TO,
                version="1",
            ),
            operator=operator,
            quantifier=Quantifier.ONE,
            completeness=CompletenessRequirement.COMPLETE,
        )
    except (TypeError, ValueError):
        problems.add(
            _SCHEMA,
            resource,
            field="responseSchema",
            family=EvaluationCapabilityFamily.ASSERTION_OPERATOR,
            alias="conformsTo",
        )
        return None


def _response_schema_selector(resource: CompiledResource) -> CompiledSelector:
    """Build the exact whole-response JSON selector used by implicit schema checks."""
    path = NoPath()
    source = SelectorSource.RESPONSE_BODY
    view = BodyViewKind.JSON
    capabilities = derive_selector_capabilities(source, path)
    return CompiledSelector(
        fingerprint=_fingerprint(
            "comparo.response-schema-selector.v1",
            resource.definition.fingerprint.digest.hex_value,
        ),
        source=source,
        path=path,
        capabilities=capabilities,
        output=derive_selector_output_contract(source, path, view),
        body_view=view,
    )


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
    if rules is None:
        if object_member(_spec(profile), "include") is None:
            problems.add(_PROFILE_EMPTY, profile, field="rules")
        return ()
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
    try:
        operator_id, operator = compile_assertion_operation(fields.get("operator"))
    except InvalidAssertionOperationError as error:
        problems.add(
            _OPERATOR,
            profile,
            rule_index=index,
            field="operator",
            family=EvaluationCapabilityFamily.ASSERTION_OPERATOR,
            alias=_safe_alias(error.error.alias),
        )
        return None
    if rule_id is None or selector is None:
        return None
    try:
        compiled = CompiledAssertionRule(
            definition=AssertionRuleDefinition(origin=_rule_origin(profile), rule_id=rule_id),
            effective_id=EffectiveRuleId(value=rule_id.value),
            fingerprint=_rule_fingerprint(profile, "assertion", rule_id, index),
            metadata=_rule_metadata(fields),
            severity=_enum_field(fields.get("severity"), Severity, Severity.BLOCKING),
            selector=selector,
            operator_id=operator_id,
            operator=operator,
            quantifier=(
                _enum_field(fields.get("quantifier"), Quantifier, Quantifier.ONE)
                if operator_uses_match_predicate(operator)
                else None
            ),
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
    if rules is None:
        if object_member(_spec(profile), "include") is None:
            problems.add(_PROFILE_EMPTY, profile, field="rules")
        return ()
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
        return None
    rule_id = _rule_id(fields.get("id"), index, profile, problems)
    if rule_id is None:
        return None
    try:
        baseline = _compile_diff_side(
            fields,
            "baseline",
            profile,
            index,
            problems,
        )
        candidate = _compile_diff_side(
            fields,
            "candidate",
            profile,
            index,
            problems,
        )
        if baseline is None or candidate is None:
            return None
        pairing = compile_pairing(
            fields.get("pairing"),
            baseline=baseline.output_contract,
            candidate=candidate.output_contract,
        )
        comparator = compile_comparator(fields.get("comparator"))
        return CompiledDiffRule(
            definition=DiffRuleDefinition(origin=_rule_origin(profile), rule_id=rule_id),
            effective_id=EffectiveRuleId(value=rule_id.value),
            fingerprint=_rule_fingerprint(profile, "diff", rule_id, index),
            metadata=_rule_metadata(fields),
            severity=_enum_field(fields.get("severity"), DiffSeverity, DiffSeverity.BLOCKING),
            completeness=_enum_field(
                fields.get("completeness"),
                DiffCompletenessRequirement,
                DiffCompletenessRequirement.COMPLETE,
            ),
            pipeline=DiffPipeline(
                baseline=baseline,
                candidate=candidate,
                pairing=pairing,
                comparator=comparator,
            ),
        )
    except InvalidDiffOperationError as error:
        family, field = _diff_problem_family(error.error.family)
        problems.add(
            _OPERATOR,
            profile,
            rule_index=index,
            field=field,
            family=family,
            alias=_safe_alias(error.error.alias),
        )
        return None
    except (TypeError, ValueError):
        problems.add(_RULE_SHAPE, profile, rule_index=index)
        return None


def _compile_diff_side(
    fields: dict[str, ValueExpression],
    name: str,
    profile: CompiledResource | InlineResource,
    index: int,
    problems: _Problems,
) -> SidePipeline | None:
    """Compile a shared pipeline or one explicit asymmetric side override."""
    side_expression = fields.get(name)
    side_fields: dict[str, ValueExpression] = {}
    if side_expression is not None:
        if type(side_expression) is not ObjectExpr:
            problems.add(_RULE_SHAPE, profile, rule_index=index, field=name)
            return None
        side_fields = _expression_fields(side_expression)
        if not set(side_fields) <= {"selector", "transforms", "reducer"}:
            problems.add(_RULE_FIELD, profile, rule_index=index, field=name)
            return None
    selector_expression = side_fields.get("selector", fields.get("selector"))
    selector = _selector_value(
        selector_expression,
        profile,
        index,
        problems,
        slot=name,
    )
    if selector is None:
        return None
    transforms = compile_transforms(
        side_fields.get("transforms", fields.get("transforms")),
        input_contract=selector.output,
    )
    reducer = compile_reducer(
        side_fields.get("reducer", fields.get("reducer")),
        input_contract=selector.output,
        transforms=transforms,
    )
    return SidePipeline(selector=selector, transforms=transforms, reducer=reducer)


def _compile_gate(
    prepared: PreparedRun,
    subjects: tuple[CompiledSubjectRules, ...],
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
    try:
        severities = _gate_enum_array(
            object_member(_spec(profile), "severities"),
            GateSeverity,
            (GateSeverity.BLOCKING,),
        )
        roles = _gate_enum_array(
            object_member(_spec(profile), "environmentRoles"),
            EnvironmentRole,
            (
                EnvironmentRole.BASELINE,
                EnvironmentRole.CANDIDATE,
                EnvironmentRole.OTHER,
            ),
        )
        require_complete = _gate_boolean(
            object_member(_spec(profile), "requireCompleteRun"),
            default=True,
        )
        require_assertions = _gate_boolean(
            object_member(_spec(profile), "requireAssertionCoverage"),
            default=any(item.assertions for item in subjects),
        )
        require_diffs = _gate_boolean(
            object_member(_spec(profile), "requireDiffCoverage"),
            default=(
                prepared.plan.cardinality.comparisons.value > 0
                and any(item.diffs for item in subjects)
            ),
        )
        thresholds = _gate_thresholds(
            object_member(_spec(profile), "thresholds"),
            severities,
        )
    except (KeyError, TypeError, ValueError):
        problems.add(_GATE, profile, family=EvaluationCapabilityFamily.GATE)
        return None
    return CompiledGateProfile(
        definition=profile.definition,
        severities=severities,
        environment_roles=roles,
        require_complete_run=require_complete,
        require_assertion_coverage=require_assertions,
        require_diff_coverage=require_diffs,
        thresholds=thresholds,
    )


def _gate_enum_array[EnumT: DomainStrEnum](
    value: CanonicalValue | None,
    enum_type: type[EnumT],
    default: tuple[EnumT, ...],
) -> tuple[EnumT, ...]:
    """Read a non-empty unique static enum array or retain the versioned default."""
    if value is None:
        return default
    if type(value) is not JsonArray or not value.items:
        raise ValueError("gate enum selection must be a non-empty array")
    result = tuple(
        enum_type(item.value) if type(item) is JsonString else enum_type("") for item in value.items
    )
    if len(set(result)) != len(result):
        raise ValueError("gate enum selections must be unique")
    return result


def _gate_boolean(value: CanonicalValue | None, *, default: bool) -> bool:
    """Read one static gate flag without accepting integer truthiness."""
    if value is None:
        return default
    if type(value) is not JsonBoolean:
        raise ValueError("gate flag must be a boolean")
    return value.value


def _gate_thresholds(
    value: CanonicalValue | None,
    severities: tuple[GateSeverity, ...],
) -> tuple[GateThreshold, ...]:
    """Compile exact per-severity adverse-rule allowances with zero defaults."""
    zero = Count(value=0)
    if value is None:
        return tuple(
            GateThreshold(
                severity=severity,
                violated_rules=zero,
                drifted_rules=zero,
                unavailable_rules=zero,
                incomparable_rules=zero,
                incomplete_rules=zero,
                error_rules=zero,
            )
            for severity in severities
        )
    if type(value) is not JsonArray or not value.items:
        raise ValueError("gate thresholds must be a non-empty array")
    thresholds = tuple(_gate_threshold(item) for item in value.items)
    if tuple(threshold.severity for threshold in thresholds) != severities:
        raise ValueError("gate thresholds must follow and exactly cover selected severities")
    return thresholds


def _gate_threshold(value: CanonicalValue) -> GateThreshold:
    """Compile one closed threshold object without retaining unknown fields."""
    if type(value) is not JsonObject:
        raise ValueError("gate threshold must be an object")
    fields = {member.name: member.value for member in value.members}
    expected = {
        "severity",
        "violatedRules",
        "driftedRules",
        "unavailableRules",
        "incomparableRules",
        "incompleteRules",
        "errorRules",
    }
    if set(fields) != expected or type(fields["severity"]) is not JsonString:
        raise ValueError("gate threshold fields do not match the typed schema")
    return GateThreshold(
        severity=GateSeverity(fields["severity"].value),
        violated_rules=_gate_count(fields["violatedRules"]),
        drifted_rules=_gate_count(fields["driftedRules"]),
        unavailable_rules=_gate_count(fields["unavailableRules"]),
        incomparable_rules=_gate_count(fields["incomparableRules"]),
        incomplete_rules=_gate_count(fields["incompleteRules"]),
        error_rules=_gate_count(fields["errorRules"]),
    )


def _gate_count(value: CanonicalValue) -> Count:
    """Read one exact non-negative threshold count."""
    if type(value) is not JsonInteger:
        raise ValueError("gate threshold counts must be integers")
    return Count(value=value.value)


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


def _diff_problem_family(
    family: DiffSyntaxFamily,
) -> tuple[EvaluationCapabilityFamily, str]:
    """Map the isolated syntax compiler stage to one public preflight coordinate."""
    return {
        DiffSyntaxFamily.TRANSFORM: (EvaluationCapabilityFamily.DIFF_TRANSFORM, "transforms"),
        DiffSyntaxFamily.REDUCER: (EvaluationCapabilityFamily.DIFF_REDUCER, "reducer"),
        DiffSyntaxFamily.PAIRING: (EvaluationCapabilityFamily.DIFF_PAIRING, "pairing"),
        DiffSyntaxFamily.COMPARATOR: (EvaluationCapabilityFamily.DIFF_COMPARATOR, "comparator"),
    }[family]


def _selector_value(
    expression: ValueExpression | None,
    profile: CompiledResource | InlineResource,
    index: int,
    problems: _Problems,
    *,
    slot: str = "shared",
) -> CompiledSelector | None:
    try:
        return compile_selector(
            expression,
            fingerprint=_selector_fingerprint(profile, f"rule-{index + 1}-{slot}"),
            default_body_view=BodyViewKind.JSON,
        )
    except InvalidSelectorError as error:
        problems.add(
            _SELECTOR,
            profile,
            rule_index=index,
            field="selector",
            family=EvaluationCapabilityFamily.SELECTOR,
            alias=error.error.alias,
        )
        return None


def _normalize_operation(name: str) -> str:
    prefix = "comparo.core/"
    return name[len(prefix) : -2] if name.startswith(prefix) and name.endswith("@1") else name


def _safe_alias(alias: str | None) -> str | None:
    if alias is None:
        return None
    normalized = _normalize_operation(alias)
    catalogs = (
        ASSERTION_OPERATION_ALIASES
        | DIFF_COMPARATOR_ALIASES
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


def _rule_metadata(fields: dict[str, ValueExpression]) -> Metadata:
    """Compile presentation-only rule metadata without affecting stable rule identity."""
    name = _literal_text(fields.get("name"))
    description = _literal_text(fields.get("description"))
    if ("name" in fields and name is None) or ("description" in fields and description is None):
        raise ValueError("rule name and description must be static text")
    tags_value = fields.get("tags")
    if tags_value is None:
        tags: tuple[str, ...] = ()
    elif type(tags_value) is ArrayExpr:
        maybe_tags = tuple(_literal_text(item) for item in tags_value.items)
        if any(tag is None for tag in maybe_tags):
            raise ValueError("rule tags must be static text")
        tags = tuple(tag for tag in maybe_tags if tag is not None)
    else:
        raise ValueError("rule tags must be an array")
    return Metadata(name=name, description=description, tags=tags)


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


def _prepared_predicates(
    schemas: PreparedSchemaCatalog,
    regexes: PreparedRegexCatalog,
    rule: CompiledAssertionRule,
    observed: ObservedExecution,
    control: SelectionControl,
    expressions: tuple[PreparedAssertionExpression, ...],
    digest_limits: DigestValidationLimits,
) -> tuple[PreparedPredicate, ...]:
    """Prepare pinned schema results for the matches the pure engine will evaluate."""
    if type(rule.operator) not in {SchemaOperator, RegexOperator, DigestOperator}:
        return ()
    selected = select(observed.snapshot, rule.selector, control).result
    if type(selected) is SelectionReady:  # noqa: SIM114 -- exact-type branches narrow for mypy
        matches = selected.matches
    elif type(selected) is SelectionPartial:
        matches = selected.matches
    else:
        return ()
    if type(rule.operator) is SchemaOperator:
        return tuple(
            schemas.predicate(
                rule.operator,
                match,
                observed.observation.execution_id,
                rule.selector.fingerprint,
            )
            for match in matches
        )
    if type(rule.operator) is RegexOperator:
        return tuple(
            regexes.predicate(
                rule.operator,
                match,
                observed.observation.execution_id,
                rule.selector.fingerprint,
            )
            for match in matches
        )
    if type(rule.operator) is DigestOperator:
        operand = next(
            (
                item.result
                for item in expressions
                if item.expression == rule.operator.expected
                and type(item.result) is AssertionOperandReady
            ),
            None,
        )
        if type(operand) is not AssertionOperandReady:
            return ()
        return tuple(
            prepare_digest_predicate(
                rule.operator,
                match,
                operand,
                observed.observation.execution_id,
                rule.selector.fingerprint,
                digest_limits,
            )
            for match in matches
        )
    return ()  # pragma: no cover - guarded by the prepared-capability type check above


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
    "CompiledSubjectRules",
    "ConfigurationEvaluationPreparation",
    "EvaluationBindingResult",
    "EvaluationCapabilityFamily",
    "EvaluationPreflightResult",
    "EvaluationRuleCatalogResult",
    "EvaluationRuleLimits",
    "RejectedEvaluationCapability",
    "bind_configuration_evaluation",
    "compile_configuration_rule_catalog",
    "preflight_configuration_evaluation",
)
