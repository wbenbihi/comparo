"""Exercise semantic pipelines, direction, coverage, and aggregate diff facts."""

from __future__ import annotations

from uuid import UUID

import pytest

from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import Exclusion
from comparo.core.domain.diffs.operations import StreamProjection
from comparo.core.domain.diffs.operations import StreamSemanticSource
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import DiffRuleDefinition
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.rules import DirectedEnvironmentPair
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CollectionComparisonKind
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.diffs.strategies import UnorderedPairing
from comparo.core.domain.diffs.strategies import UnorderedPairingKind
from comparo.core.domain.identity import ComparisonId
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
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="2" * 64))


def _operation(family: DiffOperationFamily, name: str) -> DiffOperationIdentity:
    return DiffOperationIdentity(
        namespace="comparo.core",
        family=family,
        name=name,
        version="1",
    )


def _selector(source: SelectorSource = SelectorSource.STREAM_RECORDS) -> CompiledSelector:
    cardinality = (
        SelectorCardinality.ZERO_OR_MORE
        if source is SelectorSource.STREAM_RECORDS
        else SelectorCardinality.EXACTLY_ONE
    )
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=source,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=cardinality,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=tuple(JSON_VALUE_KINDS),
            cardinality=cardinality,
        ),
    )


def _item_selector() -> CompiledItemSelector:
    capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.EXACTLY_ONE,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )
    return CompiledItemSelector(
        fingerprint=_fingerprint(),
        input=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
        path=NoPath(),
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
    )


def _environment(name: str) -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="demo"),
        kind=ResourceKind.ENVIRONMENT,
        resource_id=ResourceId(value=name),
    )


def _application() -> DiffApplication:
    profile = DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="demo"),
            kind=ResourceKind.DIFF_PROFILE,
            resource_id=ResourceId(value="semantic"),
        ),
        fingerprint=_fingerprint(),
    )
    definition = DiffRuleDefinition(origin=profile, rule_id=RuleId(value="stream-shape"))
    pair = DirectedEnvironmentPair(
        comparison_id=ComparisonId(value=UUID(int=10)),
        baseline_environment=_environment("prod"),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=11)),
        candidate_environment=_environment("staging"),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=12)),
    )
    return DiffApplication(
        application_id=RuleApplicationId(value=UUID(int=13)),
        definition=definition,
        effective_id=EffectiveRuleId(value="stream-shape"),
        rule_fingerprint=_fingerprint(),
        pair=pair,
    )


def _rule() -> CompiledDiffRule:
    side = SidePipeline(selector=_selector())
    return CompiledDiffRule(
        definition=_application().definition,
        effective_id=EffectiveRuleId(value="stream-shape"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="Stream semantics"),
        severity=DiffSeverity.BLOCKING,
        completeness=DiffCompletenessRequirement.COMPLETE,
        pipeline=DiffPipeline(
            baseline=side,
            candidate=side,
            pairing=OrderedPairing(
                identity=_operation(DiffOperationFamily.PAIRING, "ordered"),
            ),
            comparator=ExactComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "exact"),
            ),
        ),
    )


def test_stream_pipeline_can_reduce_semantics_without_chunk_identity() -> None:
    projection = StreamProjection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
        source=StreamSemanticSource.SSE_JSON_DATA,
        selector=_item_selector(),
    )
    reducer = Reducer(
        identity=_operation(DiffOperationFamily.REDUCER, "multiset"),
        kind=ReducerKind.MULTISET,
        empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
    )
    pipeline = SidePipeline(selector=_selector(), transforms=(projection,), reducer=reducer)

    assert pipeline.transforms == (projection,)
    assert pipeline.reducer == reducer


def test_exclusion_keeps_all_unlisted_fields_in_scope() -> None:
    exclusion = Exclusion(
        identity=_operation(DiffOperationFamily.TRANSFORM, "exclude"),
        pointers=(JsonPointer(value="/id"), JsonPointer(value="/createdAt")),
    )

    assert exclusion.pointers == (JsonPointer(value="/id"), JsonPointer(value="/createdAt"))


def test_set_and_multiset_strategies_are_explicit() -> None:
    pairing = UnorderedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "multiset"),
        kind=UnorderedPairingKind.MULTISET,
    )
    comparator = CollectionComparator(
        identity=_operation(DiffOperationFamily.COMPARATOR, "multiset"),
        kind=CollectionComparisonKind.MULTISET,
    )

    assert pairing.kind is UnorderedPairingKind.MULTISET
    assert comparator.kind is CollectionComparisonKind.MULTISET


def test_directed_pair_rejects_same_environment() -> None:
    environment = _environment("prod")
    with pytest.raises(ValueError, match="distinct"):
        DirectedEnvironmentPair(
            comparison_id=ComparisonId(value=UUID(int=14)),
            baseline_environment=environment,
            baseline_execution_id=EnvironmentExecutionId(value=UUID(int=15)),
            candidate_environment=environment,
            candidate_execution_id=EnvironmentExecutionId(value=UUID(int=16)),
        )


def test_equivalent_requires_nonempty_coverage_and_exact_counts() -> None:
    fact = DiffRuleFact(
        fact_id=FactId(value=UUID(int=17)),
        application=_application(),
        rule=_rule(),
        outcome=Equivalent(),
        counts=DiffFactCounts(
            same=Count(value=1),
            changed=Count(value=0),
            added=Count(value=0),
            removed=Count(value=0),
            ignored=Count(value=0),
            incomparable=Count(value=0),
        ),
        coverage=DiffCoverage(
            baseline_selected=Count(value=1),
            candidate_selected=Count(value=1),
            baseline_derived=Count(value=1),
            candidate_derived=Count(value=1),
            paired=Count(value=1),
            compared=Count(value=1),
            ignored=Count(value=0),
            not_evaluated=Count(value=0),
        ),
        evidence=DiffEvidence(retained=(), omitted=Count(value=1)),
    )

    assert type(fact.outcome) is Equivalent
