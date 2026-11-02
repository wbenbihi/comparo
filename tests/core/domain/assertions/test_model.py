"""Exercise typed assertion rules, applications, outcomes, and evidence."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import AssertionRuleDefinition
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.expressions import LiteralExpr
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
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonInteger
from tests.core.domain.assertions._invariant_support import _evidence_exhaustion


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="1" * 64))


def _definition(kind: ResourceKind) -> DefinitionRef:
    return DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="demo"),
            kind=kind,
            resource_id=ResourceId(value="profile"),
        ),
        fingerprint=_fingerprint(),
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


def _operator() -> EqualityOperator:
    provenance = ValueProvenance(
        entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),),
    )
    return EqualityOperator(
        relation=EqualityRelation.EQUALS,
        expected=LiteralExpr(value=JsonInteger(value=200), provenance=provenance),
    )


def _operator_id(kind: AssertionOperatorKind) -> AssertionOperatorIdentity:
    return AssertionOperatorIdentity(namespace="comparo.core", kind=kind, version="1")


def _rule_definition() -> AssertionRuleDefinition:
    return AssertionRuleDefinition(
        origin=_definition(ResourceKind.ASSERTION_PROFILE),
        rule_id=RuleId(value="status-ok"),
    )


def _compiled_rule() -> CompiledAssertionRule:
    return CompiledAssertionRule(
        definition=_rule_definition(),
        effective_id=EffectiveRuleId(value="status-ok"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="Status is successful"),
        severity=Severity.BLOCKING,
        selector=_selector(),
        operator_id=_operator_id(AssertionOperatorKind.EQUALITY),
        operator=_operator(),
        quantifier=Quantifier.ONE,
        completeness=CompletenessRequirement.COMPLETE,
    )


def test_compiled_rule_rejects_mismatched_operator_identity() -> None:
    with pytest.raises(ValueError, match="does not match"):
        CompiledAssertionRule(
            definition=_rule_definition(),
            effective_id=EffectiveRuleId(value="status-ok"),
            fingerprint=_fingerprint(),
            metadata=Metadata(name="Status is successful"),
            severity=Severity.BLOCKING,
            selector=_selector(),
            operator_id=_operator_id(AssertionOperatorKind.REGEX),
            operator=_operator(),
            quantifier=Quantifier.ONE,
            completeness=CompletenessRequirement.COMPLETE,
        )


def test_decode_rule_is_bound_to_its_exact_whole_body_view() -> None:
    path = NoPath()
    capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_BODY, path)
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=capabilities.cardinality,
        ),
        body_view=BodyViewKind.JSON,
    )
    operator = DecodeOperator(view=BodyViewKind.JSON)
    decode = msgspec.structs.replace(
        _compiled_rule(),
        selector=selector,
        operator_id=_operator_id(AssertionOperatorKind.DECODES_AS),
        operator=operator,
        quantifier=None,
    )

    assert decode.operator == operator
    with pytest.raises(ValueError, match="body selector"):
        msgspec.structs.replace(decode, selector=_selector())
    with pytest.raises(ValueError, match="view must match"):
        msgspec.structs.replace(decode, operator=DecodeOperator(view=BodyViewKind.AUTO))

    nested_path = JsonPointerPath(pointer=JsonPointer(value="/id"))
    nested_capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_BODY, nested_path)
    nested = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=nested_path,
        capabilities=nested_capabilities,
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=nested_capabilities.cardinality,
        ),
        body_view=BodyViewKind.JSON,
    )
    with pytest.raises(ValueError, match="whole-body"):
        msgspec.structs.replace(decode, selector=nested)


def test_reusable_rule_requires_assertion_profile_origin() -> None:
    with pytest.raises(ValueError, match="AssertionProfile"):
        AssertionRuleDefinition(
            origin=_definition(ResourceKind.DIFF_PROFILE),
            rule_id=RuleId(value="wrong-owner"),
        )


def test_parent_fact_keeps_exact_counts_separate_from_empty_evidence() -> None:
    definition = _rule_definition()
    application = AssertionApplication(
        application_id=RuleApplicationId(value=UUID(int=2)),
        definition=definition,
        effective_id=EffectiveRuleId(value="status-ok"),
        rule_fingerprint=_fingerprint(),
        environment=_definition(ResourceKind.ENVIRONMENT),
        environment_execution_id=EnvironmentExecutionId(value=UUID(int=3)),
    )
    counts = AssertionCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=1),
        violated=Count(value=0),
        errors=Count(value=0),
    )
    fact = AssertionFact(
        fact_id=FactId(value=UUID(int=4)),
        application=application,
        rule=_compiled_rule(),
        outcome=Satisfied(),
        counts=counts,
        evidence=AssertionEvidence(
            retained=(), omitted=Count(value=1), truncation=_evidence_exhaustion()
        ),
    )

    assert fact.counts.evaluated.value == 1
    assert fact.evidence.retained == ()


def test_inconsistent_exact_counts_are_rejected() -> None:
    with pytest.raises(ValueError, match="evaluated"):
        AssertionCounts(
            selected=Count(value=2),
            evaluated=Count(value=2),
            satisfied=Count(value=1),
            violated=Count(value=0),
            errors=Count(value=0),
        )
