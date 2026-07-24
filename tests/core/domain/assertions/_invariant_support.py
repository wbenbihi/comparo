"""Cover every assertion operator and direct-construction outcome invariant."""

from __future__ import annotations

from uuid import UUID

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import CompiledSchema
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaRelation
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.extended_operators import TokenOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.assertions.operators import ContainsMode
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import CountRelation
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.operators import ExactValueType
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import KeySetRelation
from comparo.core.domain.assertions.operators import LengthOperator
from comparo.core.domain.assertions.operators import MembershipOperator
from comparo.core.domain.assertions.operators import MembershipRelation
from comparo.core.domain.assertions.operators import OrderingOperator
from comparo.core.domain.assertions.operators import OrderingRelation
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.assertions.operators import TypeOperator
from comparo.core.domain.assertions.regex import RegexCompilerIdentity
from comparo.core.domain.assertions.regex import RegexDialect
from comparo.core.domain.assertions.regex import RegexMode
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import AssertionRuleDefinition
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonInteger


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="5" * 64))


def _literal(value: int = 1) -> LiteralExpr:
    return LiteralExpr(
        value=JsonInteger(value=value),
        provenance=ValueProvenance(
            entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),),
        ),
    )


def _selector() -> CompiledSelector:
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_STATUS,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=(ValueKind.INTEGER,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
    )


def _regex(pattern: str = "^[0-9]+$", *, negate: bool = False) -> RegexOperator:
    return RegexOperator(
        pattern=pattern,
        mode=RegexMode.FULL,
        dialect=RegexDialect.RE2,
        compiler=RegexCompilerIdentity(
            namespace="comparo.core",
            name="re2",
            version="1",
        ),
        compiled_fingerprint=_fingerprint(),
        negate=negate,
    )


def _compiled_rule() -> CompiledAssertionRule:
    operator = EqualityOperator(relation=EqualityRelation.EQUALS, expected=_literal(200))
    return CompiledAssertionRule(
        definition=_rule_definition(),
        effective_id=EffectiveRuleId(value="status"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="HTTP status"),
        severity=Severity.BLOCKING,
        selector=_selector(),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.EQUALITY,
            version="1",
        ),
        operator=operator,
        quantifier=Quantifier.ONE,
        completeness=CompletenessRequirement.COMPLETE,
    )


def _assertion_fact(
    *,
    rule: CompiledAssertionRule,
    outcome: Satisfied | Violated | AssertionError | Skipped | AssertionUnavailable,
    counts: AssertionCounts,
) -> AssertionFact:
    return AssertionFact(
        fact_id=FactId(value=UUID(int=799)),
        application=_application(),
        rule=rule,
        outcome=outcome,
        counts=counts,
        evidence=AssertionEvidence(retained=(), omitted=Count(value=counts.evaluated.value)),
    )


def _rule_definition() -> AssertionRuleDefinition:
    return AssertionRuleDefinition(
        origin=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="demo"),
                kind=ResourceKind.ASSERTION_PROFILE,
                resource_id=ResourceId(value="api"),
            ),
            fingerprint=_fingerprint(),
        ),
        rule_id=RuleId(value="status"),
    )


def _application() -> AssertionApplication:
    return AssertionApplication(
        application_id=RuleApplicationId(value=UUID(int=49)),
        definition=_rule_definition(),
        effective_id=EffectiveRuleId(value="status"),
        rule_fingerprint=_fingerprint(),
        environment=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="demo"),
                kind=ResourceKind.ENVIRONMENT,
                resource_id=ResourceId(value="prod"),
            ),
            fingerprint=_fingerprint(),
        ),
        environment_execution_id=EnvironmentExecutionId(value=UUID(int=48)),
    )


def _operators() -> tuple[AssertionOperator, ...]:
    remote_schema = CompiledSchema(
        fingerprint=_fingerprint(),
        remote_uri=NamedCapabilityTarget(
            kind=CapabilityTargetKind.SCHEMA_SOURCE,
            identifier="https://schemas.example.test/response.json",
        ),
    )
    return (
        EqualityOperator(relation=EqualityRelation.EQUALS, expected=_literal()),
        MembershipOperator(relation=MembershipRelation.IN, choices=(_literal(),)),
        TypeOperator(expected=ExactValueType.INTEGER),
        OrderingOperator(relation=OrderingRelation.GREATER, expected=_literal()),
        BetweenOperator(
            lower=_literal(0),
            upper=_literal(2),
            include_lower=True,
            include_upper=False,
        ),
        ApproximateOperator(expected=_literal(), absolute_tolerance=_literal(0)),
        StringOperator(relation=StringRelation.CONTAINS, expected=_literal()),
        _regex(),
        ContainsOperator(mode=ContainsMode.ONE, expected=(_literal(),)),
        KeySetOperator(relation=KeySetRelation.HAS, keys=("id",)),
        LengthOperator(relation=CountRelation.AT_LEAST, expected=Count(value=1)),
        SchemaOperator(
            schema=remote_schema,
            default_dialect=NamedCapabilityTarget(
                kind=CapabilityTargetKind.SCHEMA_SOURCE,
                identifier="https://json-schema.org/draft/2020-12/schema",
            ),
        ),
        DecodeOperator(view=BodyViewKind.JSON),
        MediaTypeOperator(
            relation=MediaRelation.SUFFIX_MATCH,
            expected=MediaType(type="application", subtype="*+json"),
        ),
        TokenOperator(token=MediaTypeTokenValue(value="gzip")),
        DigestOperator(algorithm=DigestAlgorithm.SHA256, expected=_literal()),
        CardinalityOperator(minimum=Count(value=1), maximum=Count(value=2)),
    )
