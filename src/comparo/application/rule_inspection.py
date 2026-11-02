"""Value-safe, paged inspection of the exact rules admitted for a prepared run."""

from __future__ import annotations

from typing import Protocol
from typing import final

import msgspec

from comparo.application.evaluation_binding import compile_configuration_rule_catalog
from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.preflight import PreparedProjectPlan
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.occurrences import AllAttemptsOccurrence
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import AttemptOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.occurrences import TerminalOccurrence
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import StaticFieldPath


@final
class RuleOriginInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Stable named or inline origin for one compiled effective rule."""

    resource: ResourceCoordinate | None = None
    inline_owner: tuple[str, str, str] | None = None
    fragment: str | None = None

    def __post_init__(self) -> None:
        """Keep named and inline origin coordinates disjoint."""
        named = self.resource is not None and self.inline_owner is None and self.fragment is None
        inline = (
            self.resource is None and self.inline_owner is not None and self.fragment is not None
        )
        if named == inline:
            raise ValueError("rule origin must be exactly named or inline")
        if self.resource is not None and type(self.resource) is not ResourceCoordinate:
            raise TypeError("named rule origin must be a ResourceCoordinate")
        if self.inline_owner is not None and (
            type(self.inline_owner) is not tuple
            or len(self.inline_owner) != 3
            or any(type(item) is not str or not item for item in self.inline_owner)
        ):
            raise ValueError("inline rule owner must be a complete resource identity")
        if self.fragment is not None and (type(self.fragment) is not str or not self.fragment):
            raise ValueError("inline rule fragment must be non-empty text")


@final
class RuleOperationInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Namespaced versioned assertion or diff operation identity."""

    namespace: str
    family: str
    name: str
    version: str

    def __post_init__(self) -> None:
        """Require complete bounded operation coordinates."""
        if any(
            type(value) is not str or not value or len(value) > 127
            for value in (self.namespace, self.family, self.name, self.version)
        ):
            raise ValueError("rule operation identity must be bounded non-empty text")


@final
class RuleSelectorPathInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Canonical selector-path syntax without parser or AST internals."""

    dialect: str
    expression: str
    field_occurrence: int | None = None
    field_projection: str | None = None

    def __post_init__(self) -> None:
        """Require a stable dialect and coherent optional HTTP field details."""
        if type(self.dialect) is not str or not self.dialect:
            raise ValueError("selector path dialect must be non-empty text")
        if type(self.expression) is not str:
            raise TypeError("selector path expression must be text")
        if self.field_occurrence is not None and (
            type(self.field_occurrence) is not int or self.field_occurrence < 0
        ):
            raise ValueError("selector HTTP field occurrence must be non-negative or absent")
        if self.field_projection is not None and (
            type(self.field_projection) is not str or not self.field_projection
        ):
            raise ValueError("selector HTTP field projection must be text or absent")


@final
class RuleSelectorInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Complete value-free selector contract used by one rule stage."""

    source: str
    path: RuleSelectorPathInspection
    body_view: str | None
    occurrence: str
    cardinality: str
    value_kinds: tuple[str, ...]
    access: str
    may_duplicate: bool
    uses_descendant: bool
    uses_filter: bool
    function_dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require typed, immutable selector semantics."""
        if type(self.path) is not RuleSelectorPathInspection:
            raise TypeError("rule selector path must be typed")
        for value in (self.source, self.occurrence, self.cardinality, self.access):
            if type(value) is not str or not value:
                raise ValueError("rule selector labels must be non-empty text")
        if self.body_view is not None and (type(self.body_view) is not str or not self.body_view):
            raise ValueError("rule selector body view must be text or absent")
        for values in (self.value_kinds, self.function_dependencies):
            if type(values) is not tuple or any(type(item) is not str for item in values):
                raise TypeError("rule selector collections must be immutable text tuples")
        if any(
            type(value) is not bool
            for value in (self.may_duplicate, self.uses_descendant, self.uses_filter)
        ):
            raise TypeError("rule selector capability markers must be booleans")


@final
class DiffSideInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One complete pre-run diff side pipeline."""

    selector: RuleSelectorInspection
    transforms: tuple[RuleOperationInspection, ...]
    reducer: RuleOperationInspection | None

    def __post_init__(self) -> None:
        """Require a selector and immutable typed operation sequence."""
        if type(self.selector) is not RuleSelectorInspection:
            raise TypeError("diff side selector must be typed")
        if type(self.transforms) is not tuple or any(
            type(item) is not RuleOperationInspection for item in self.transforms
        ):
            raise TypeError("diff side transforms must be immutable and typed")
        if self.reducer is not None and type(self.reducer) is not RuleOperationInspection:
            raise TypeError("diff side reducer must be typed or absent")


@final
class PlanRuleInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One exact compiled assertion or diff rule without operand values."""

    ordinal: int
    subject: ResourceCoordinate
    family: str
    origin: RuleOriginInspection
    rule_id: str
    effective_rule_id: str
    fingerprint: str
    name: str | None
    description: str | None
    tags: tuple[str, ...]
    severity: str
    completeness: str
    selector: RuleSelectorInspection | None = None
    operator: RuleOperationInspection | None = None
    quantifier: str | None = None
    baseline: DiffSideInspection | None = None
    candidate: DiffSideInspection | None = None
    pairing: RuleOperationInspection | None = None
    comparator: RuleOperationInspection | None = None

    def __post_init__(self) -> None:  # noqa: C901 - exact assertion/diff tagged union
        """Keep assertion and diff inspection products structurally disjoint."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("plan rule ordinal must be non-negative")
        if type(self.subject) is not ResourceCoordinate:
            raise TypeError("plan rule subject must be a ResourceCoordinate")
        if type(self.origin) is not RuleOriginInspection:
            raise TypeError("plan rule origin must be typed")
        for value in (
            self.family,
            self.rule_id,
            self.effective_rule_id,
            self.fingerprint,
            self.severity,
            self.completeness,
        ):
            if type(value) is not str or not value:
                raise ValueError("plan rule identity fields must be non-empty text")
        for optional_value in (self.name, self.description, self.quantifier):
            if optional_value is not None and type(optional_value) is not str:
                raise TypeError("plan rule optional labels must be text or absent")
        if type(self.tags) is not tuple or any(type(item) is not str for item in self.tags):
            raise TypeError("plan rule tags must be an immutable text tuple")
        assertion = self.family == "assertion"
        diff = self.family == "diff"
        if not assertion and not diff:
            raise ValueError("plan rule family is unsupported")
        assertion_fields = (self.selector, self.operator)
        diff_fields = (self.baseline, self.candidate, self.pairing, self.comparator)
        if assertion and (any(item is None for item in assertion_fields) or any(diff_fields)):
            raise ValueError("assertion rule inspection has contradictory fields")
        if diff and (any(item is None for item in diff_fields) or any(assertion_fields)):
            raise ValueError("diff rule inspection has contradictory fields")
        if diff and self.quantifier is not None:
            raise ValueError("diff rule inspection cannot retain an assertion quantifier")


@final
class PlanRuleWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One stable page from the exact admitted pre-run rule sequence."""

    plan_fingerprint: str
    start_ordinal: int
    total_rules: int
    rules: tuple[PlanRuleInspection, ...]
    next_ordinal: int | None

    def __post_init__(self) -> None:
        """Require a contiguous page and exact continuation cursor."""
        if type(self.start_ordinal) is not int or self.start_ordinal < 0:
            raise ValueError("plan rule cursor must be non-negative")
        if type(self.total_rules) is not int or self.total_rules < 0:
            raise ValueError("plan rule total must be non-negative")
        if type(self.rules) is not tuple or any(
            type(item) is not PlanRuleInspection for item in self.rules
        ):
            raise TypeError("plan rule page must be immutable and typed")
        if tuple(item.ordinal for item in self.rules) != tuple(
            range(self.start_ordinal, self.start_ordinal + len(self.rules))
        ):
            raise ValueError("plan rule page must be contiguous")
        consumed = self.start_ordinal + len(self.rules)
        expected = consumed if consumed < self.total_rules else None
        if self.next_ordinal != expected:
            raise ValueError("plan rule continuation contradicts its page")


class _DiffOperation(Protocol):
    @property
    def identity(self) -> DiffOperationIdentity:
        """Return the versioned diff operation identity."""


def inspect_plan_rule_window(
    project: PreparedProjectPlan,
    *,
    start_ordinal: int,
    maximum_rules: int,
) -> PlanRuleWindow:
    """Project one caller-bounded page from deterministic compiler output."""
    if type(project) is not PreparedProjectPlan:
        raise TypeError("plan rule inspection requires PreparedProjectPlan")
    if type(start_ordinal) is not int or start_ordinal < 0:
        raise ValueError("plan rule cursor must be non-negative")
    if type(maximum_rules) is not int or maximum_rules < 1:
        raise ValueError("plan rule page size must be a positive integer")
    catalog = compile_configuration_rule_catalog(
        project.prepared,
        policy=project.policy,
    )
    if catalog.diagnostics:
        raise ValueError("prepared plan no longer admits its evaluation rule catalog")
    flattened: list[PlanRuleInspection] = []
    for subject in catalog.subjects:
        coordinate = _coordinate(subject.subject)
        for assertion_rule in subject.assertions:
            flattened.append(_assertion(len(flattened), coordinate, assertion_rule))
        for diff_rule in subject.diffs:
            flattened.append(_diff(len(flattened), coordinate, diff_rule))
    rules = tuple(flattened[start_ordinal : start_ordinal + maximum_rules])
    total = len(flattened)
    return PlanRuleWindow(
        plan_fingerprint=project.prepared.plan.fingerprint.digest.hex_value,
        start_ordinal=start_ordinal,
        total_rules=total,
        rules=rules,
        next_ordinal=(start_ordinal + len(rules) if start_ordinal + len(rules) < total else None),
    )


def _assertion(
    ordinal: int,
    subject: ResourceCoordinate,
    rule: CompiledAssertionRule,
) -> PlanRuleInspection:
    return PlanRuleInspection(
        ordinal=ordinal,
        subject=subject,
        family="assertion",
        origin=_origin(rule.definition.origin),
        rule_id=rule.definition.rule_id.value,
        effective_rule_id=rule.effective_id.value,
        fingerprint=rule.fingerprint.digest.hex_value,
        name=rule.metadata.name,
        description=rule.metadata.description,
        tags=rule.metadata.tags,
        severity=rule.severity.value,
        completeness=rule.completeness.value,
        selector=_selector(rule.selector),
        operator=RuleOperationInspection(
            namespace=rule.operator_id.namespace,
            family="assertion-operator",
            name=rule.operator_id.kind.value,
            version=rule.operator_id.version,
        ),
        quantifier=None if rule.quantifier is None else rule.quantifier.value,
    )


def _diff(
    ordinal: int,
    subject: ResourceCoordinate,
    rule: CompiledDiffRule,
) -> PlanRuleInspection:
    return PlanRuleInspection(
        ordinal=ordinal,
        subject=subject,
        family="diff",
        origin=_origin(rule.definition.origin),
        rule_id=rule.definition.rule_id.value,
        effective_rule_id=rule.effective_id.value,
        fingerprint=rule.fingerprint.digest.hex_value,
        name=rule.metadata.name,
        description=rule.metadata.description,
        tags=rule.metadata.tags,
        severity=rule.severity.value,
        completeness=rule.completeness.value,
        baseline=_side(rule.pipeline.baseline),
        candidate=_side(rule.pipeline.candidate),
        pairing=_operation(rule.pipeline.pairing),
        comparator=_operation(rule.pipeline.comparator),
    )


def _side(value: SidePipeline) -> DiffSideInspection:
    return DiffSideInspection(
        selector=_selector(value.selector),
        transforms=tuple(_operation(item) for item in value.transforms),
        reducer=None if value.reducer is None else _operation(value.reducer),
    )


def _operation(value: _DiffOperation) -> RuleOperationInspection:
    identity = value.identity
    return RuleOperationInspection(
        namespace=identity.namespace,
        family=identity.family.value,
        name=identity.name,
        version=identity.version,
    )


def _selector(value: CompiledSelector) -> RuleSelectorInspection:
    capabilities = value.capabilities
    return RuleSelectorInspection(
        source=value.source.value,
        path=_path(value),
        body_view=None if value.body_view is None else value.body_view.value,
        occurrence=_occurrence(value),
        cardinality=capabilities.cardinality.value,
        value_kinds=tuple(item.value for item in value.output.kinds),
        access=capabilities.access.value,
        may_duplicate=capabilities.may_duplicate,
        uses_descendant=capabilities.uses_descendant,
        uses_filter=capabilities.uses_filter,
        function_dependencies=capabilities.function_dependencies,
    )


def _path(selector: CompiledSelector) -> RuleSelectorPathInspection:
    path = selector.path
    if type(path) is NoPath:
        return RuleSelectorPathInspection(dialect="none", expression="")
    if type(path) is StaticFieldPath:
        return RuleSelectorPathInspection(
            dialect="comparo.static-fields",
            expression=".".join(path.segments),
        )
    if type(path) is JsonPointerPath:
        return RuleSelectorPathInspection(dialect="rfc6901", expression=path.pointer.value)
    if type(path) is JsonPathExpression:
        return RuleSelectorPathInspection(dialect="rfc9535", expression=path.canonical)
    if type(path) is HttpFieldPath:
        return RuleSelectorPathInspection(
            dialect="comparo.http-field",
            expression=path.name,
            field_occurrence=None if path.occurrence is None else path.occurrence.value,
            field_projection=path.projection.value,
        )
    raise TypeError("compiled selector path is unsupported")  # pragma: no cover


def _occurrence(selector: CompiledSelector) -> str:
    occurrence = selector.occurrence
    if type(occurrence) is TerminalOccurrence:
        return "terminal"
    if type(occurrence) is AllAttemptsOccurrence:
        return "all-attempts"
    if type(occurrence) is AllExchangesOccurrence:
        return "all-exchanges"
    if type(occurrence) is AttemptOccurrence:
        return f"attempt:{occurrence.attempt_id.value}"
    if type(occurrence) is ExchangeOccurrence:
        return f"exchange:{occurrence.exchange_id.value}"
    raise TypeError("compiled selector occurrence is unsupported")  # pragma: no cover


def _origin(value: DefinitionRef | InlineDefinitionOrigin) -> RuleOriginInspection:
    if type(value) is DefinitionRef:
        return RuleOriginInspection(resource=_coordinate(value))
    owner = value.owner
    return RuleOriginInspection(
        inline_owner=(owner.namespace.value, owner.kind.value, owner.resource_id.value),
        fragment=value.fragment.value,
    )


def _coordinate(value: DefinitionRef) -> ResourceCoordinate:
    identity = value.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=value.fingerprint.digest.hex_value,
    )


__all__ = (
    "DiffSideInspection",
    "PlanRuleInspection",
    "PlanRuleWindow",
    "RuleOperationInspection",
    "RuleOriginInspection",
    "RuleSelectorInspection",
    "RuleSelectorPathInspection",
    "inspect_plan_rule_window",
)
