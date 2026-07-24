"""Cover diff operation catalogs, directionality, and aggregate invariants."""

from __future__ import annotations

from uuid import UUID

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
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import ItemEqualityPredicate
from comparo.core.domain.selectors.items import ItemPredicateIdentity
from comparo.core.domain.selectors.items import ItemPredicateKind
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonString


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="6" * 64))


def _operation(family: DiffOperationFamily, name: str) -> DiffOperationIdentity:
    return DiffOperationIdentity(
        namespace="comparo.core",
        family=family,
        name=name,
        version="1",
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


def _boolean_selector() -> CompiledSelector:
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.SCENARIO_STEP,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=(ValueKind.BOOLEAN,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
    )


def _object_selector() -> CompiledSelector:
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.SCENARIO_STEP,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=(ValueKind.OBJECT,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
    )


def _plural_selector() -> CompiledSelector:
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.STREAM_RECORDS,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_MORE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=tuple(JSON_VALUE_KINDS),
            cardinality=SelectorCardinality.ZERO_OR_MORE,
        ),
    )


def _optional_selector() -> CompiledSelector:
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=JsonPointerPath(pointer=JsonPointer(value="/id")),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=SelectorCardinality.ZERO_OR_ONE,
        ),
        body_view=BodyViewKind.JSON,
    )


def _item_selector(
    *,
    output_kind: ValueKind = ValueKind.STRING,
) -> CompiledItemSelector:
    capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.EXACTLY_ONE,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )
    path = JsonPathExpression(
        canonical="$.value",
        fingerprint=_fingerprint(),
        capabilities=capabilities,
    )
    return CompiledItemSelector(
        fingerprint=_fingerprint(),
        input=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(output_kind,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
    )


def _optional_item_selector() -> CompiledItemSelector:
    capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_ONE,
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
        path=JsonPointerPath(pointer=JsonPointer(value="/id")),
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.STRING,),
            cardinality=SelectorCardinality.ZERO_OR_ONE,
        ),
    )


def _plural_item_selector() -> CompiledItemSelector:
    capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_MORE,
        access=SelectionAccess.INDEXED,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )
    path = JsonPathExpression(
        canonical="$[*]",
        fingerprint=_fingerprint(),
        capabilities=capabilities,
    )
    return CompiledItemSelector(
        fingerprint=_fingerprint(),
        input=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.STRING,),
            cardinality=SelectorCardinality.ZERO_OR_MORE,
        ),
    )


def _predicate() -> CompiledItemPredicate:
    return CompiledItemPredicate(
        identity=ItemPredicateIdentity(
            namespace="comparo.core",
            kind=ItemPredicateKind.EQUALS,
            version="1",
        ),
        selector=_item_selector(),
        operator=ItemEqualityPredicate(expected=_literal("ready")),
    )


def _literal(value: str = "") -> LiteralExpr:
    return LiteralExpr(
        value=JsonString(value=value),
        provenance=ValueProvenance(
            entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),),
        ),
    )


def _environment(name: str) -> DefinitionRef:
    return DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="demo"),
            kind=ResourceKind.ENVIRONMENT,
            resource_id=ResourceId(value=name),
        ),
        fingerprint=_fingerprint(),
    )


def _application() -> DiffApplication:
    profile = DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="demo"),
            kind=ResourceKind.DIFF_PROFILE,
            resource_id=ResourceId(value="profile"),
        ),
        fingerprint=_fingerprint(),
    )
    return DiffApplication(
        application_id=RuleApplicationId(value=UUID(int=60)),
        definition=DiffRuleDefinition(origin=profile, rule_id=RuleId(value="response")),
        effective_id=EffectiveRuleId(value="response"),
        rule_fingerprint=_fingerprint(),
        pair=DirectedEnvironmentPair(
            comparison_id=ComparisonId(value=UUID(int=61)),
            baseline_environment=_environment("prod"),
            baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
            candidate_environment=_environment("staging"),
            candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        ),
    )


def _compiled_rule(
    severity: DiffSeverity = DiffSeverity.BLOCKING,
) -> CompiledDiffRule:
    side = SidePipeline(selector=_selector())
    return CompiledDiffRule(
        definition=_application().definition,
        effective_id=EffectiveRuleId(value="response"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="Response semantics"),
        severity=severity,
        completeness=DiffCompletenessRequirement.COMPLETE,
        pipeline=DiffPipeline(
            baseline=side,
            candidate=side,
            pairing=SingularPairing(
                identity=_operation(DiffOperationFamily.PAIRING, "singular"),
            ),
            comparator=ExactComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "exact"),
            ),
        ),
    )


def _location(index: int, *, candidate: bool = False) -> ExchangeLocation:
    return ExchangeLocation(
        environment_execution_id=EnvironmentExecutionId(value=UUID(int=63 if candidate else 62)),
        source=SelectorSource.RESPONSE_STATUS,
        exchange_id=ExchangeId(value=UUID(int=index)),
        field=StaticFieldPath(segments=("response", "status")),
    )
