"""Cover diff operation catalogs, directionality, and aggregate invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

import comparo.core.domain.diffs.model as diff_model
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import Projection
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.rules import ApplicabilityExpectation
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplicability
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import DiffRuleDefinition
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.rules import DirectedEnvironmentPair
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import SingularPairing
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
from comparo.core.domain.selectors.model import CompiledSelector
from tests.core.domain.diffs._invariant_support import _application
from tests.core.domain.diffs._invariant_support import _boolean_selector
from tests.core.domain.diffs._invariant_support import _environment
from tests.core.domain.diffs._invariant_support import _fingerprint
from tests.core.domain.diffs._invariant_support import _location
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _plural_selector
from tests.core.domain.diffs._invariant_support import _selector


def test_compiled_diff_rule_owns_complete_static_pipeline() -> None:
    selector = _selector()
    side = SidePipeline(selector=selector)
    pipeline = DiffPipeline(
        baseline=side,
        candidate=side,
        pairing=SingularPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "singular"),
        ),
        comparator=ExactComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        ),
    )
    definition = _application().definition
    rule = CompiledDiffRule(
        definition=definition,
        effective_id=EffectiveRuleId(value="response"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="Response semantics"),
        severity=DiffSeverity.BLOCKING,
        completeness=DiffCompletenessRequirement.COMPLETE,
        pipeline=pipeline,
        applicability=DiffApplicability(
            baseline_selector=_boolean_selector(),
            candidate_selector=_boolean_selector(),
            expectation=ApplicabilityExpectation.TRUE,
        ),
    )
    assert rule.pipeline == pipeline
    assert diff_model.CompiledDiffRule is CompiledDiffRule
    with pytest.raises(TypeError, match="definition"):
        msgspec.structs.replace(rule, definition=cast("DiffRuleDefinition", "bad"))
    with pytest.raises(TypeError, match="effective ID"):
        msgspec.structs.replace(rule, effective_id=cast("EffectiveRuleId", "bad"))
    with pytest.raises(TypeError, match="fingerprint and metadata"):
        msgspec.structs.replace(rule, fingerprint=cast("Fingerprint", "bad"))
    with pytest.raises(TypeError, match="severity"):
        msgspec.structs.replace(rule, severity=cast("DiffSeverity", "bad"))
    with pytest.raises(TypeError, match="completeness"):
        msgspec.structs.replace(
            rule,
            completeness=cast("DiffCompletenessRequirement", "bad"),
        )
    with pytest.raises(TypeError, match="pipeline"):
        msgspec.structs.replace(rule, pipeline=cast("DiffPipeline", "bad"))
    with pytest.raises(TypeError, match="applicability"):
        msgspec.structs.replace(rule, applicability=cast("DiffApplicability", "bad"))
    with pytest.raises(TypeError, match="baseline and candidate"):
        DiffPipeline(
            baseline=cast("SidePipeline", "bad"),
            candidate=side,
            pairing=pipeline.pairing,
            comparator=pipeline.comparator,
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        SidePipeline(
            selector=selector,
            transforms=cast("tuple[Projection, ...]", []),
        )
    with pytest.raises(TypeError, match="side selector"):
        SidePipeline(selector=cast("CompiledSelector", "bad"))
    with pytest.raises(TypeError, match="unsupported transform"):
        SidePipeline(selector=selector, transforms=(cast("Projection", "bad"),))
    with pytest.raises(TypeError, match="reducer"):
        SidePipeline(selector=selector, reducer=cast("Reducer", "bad"))
    with pytest.raises(TypeError, match="pairing"):
        msgspec.structs.replace(pipeline, pairing=cast("SingularPairing", "bad"))
    with pytest.raises(TypeError, match="comparator"):
        msgspec.structs.replace(pipeline, comparator=cast("ExactComparator", "bad"))


def test_applicability_is_explicit_and_side_specific() -> None:
    applicability = DiffApplicability(
        baseline_selector=_boolean_selector(),
        candidate_selector=_boolean_selector(),
        expectation=ApplicabilityExpectation.TRUE,
    )
    assert applicability.baseline_selector == applicability.candidate_selector
    with pytest.raises(TypeError, match="baseline"):
        DiffApplicability(
            baseline_selector=cast("CompiledSelector", "bad"),
            candidate_selector=_boolean_selector(),
            expectation=ApplicabilityExpectation.TRUE,
        )
    with pytest.raises(TypeError, match="candidate"):
        msgspec.structs.replace(
            applicability,
            candidate_selector=cast("CompiledSelector", "bad"),
        )
    with pytest.raises(TypeError, match="expectation"):
        msgspec.structs.replace(
            applicability,
            expectation=cast("ApplicabilityExpectation", "true"),
        )
    with pytest.raises(ValueError, match="singular boolean"):
        msgspec.structs.replace(applicability, baseline_selector=_selector())
    with pytest.raises(ValueError, match="singular boolean"):
        msgspec.structs.replace(applicability, candidate_selector=_plural_selector())


def test_diff_rule_and_application_identity_coordinates_are_strictly_typed() -> None:
    application = _application()
    definition = application.definition
    pair = application.pair
    with pytest.raises(TypeError, match="origin"):
        DiffRuleDefinition(
            origin=cast("DefinitionRef", "bad"),
            rule_id=RuleId(value="response"),
        )
    with pytest.raises(ValueError, match="DiffProfile"):
        DiffRuleDefinition(
            origin=DefinitionRef(
                identity=_environment("prod"),
                fingerprint=_fingerprint(),
            ),
            rule_id=RuleId(value="response"),
        )
    with pytest.raises(TypeError, match="rule ID"):
        msgspec.structs.replace(definition, rule_id=cast("RuleId", "bad"))
    with pytest.raises(TypeError, match="comparison ID"):
        msgspec.structs.replace(pair, comparison_id=cast("ComparisonId", "bad"))
    with pytest.raises(TypeError, match="pair environments"):
        msgspec.structs.replace(
            pair,
            baseline_environment=cast("ResourceIdentity", "bad"),
        )
    with pytest.raises(ValueError, match="Environments"):
        msgspec.structs.replace(
            pair,
            baseline_environment=ResourceIdentity(
                namespace=NamespaceId(value="demo"),
                kind=ResourceKind.REQUEST,
                resource_id=ResourceId(value="request"),
            ),
        )
    with pytest.raises(ValueError, match="distinct"):
        msgspec.structs.replace(pair, candidate_environment=pair.baseline_environment)
    with pytest.raises(TypeError, match="baseline execution"):
        msgspec.structs.replace(
            pair,
            baseline_execution_id=cast("EnvironmentExecutionId", "bad"),
        )
    with pytest.raises(TypeError, match="candidate execution"):
        msgspec.structs.replace(
            pair,
            candidate_execution_id=cast("EnvironmentExecutionId", "bad"),
        )
    with pytest.raises(ValueError, match="distinct Environment execution IDs"):
        msgspec.structs.replace(
            pair,
            candidate_execution_id=pair.baseline_execution_id,
        )
    with pytest.raises(TypeError, match="application ID"):
        msgspec.structs.replace(
            application,
            application_id=cast("RuleApplicationId", "bad"),
        )
    with pytest.raises(TypeError, match="application definition"):
        msgspec.structs.replace(
            application,
            definition=cast("DiffRuleDefinition", "bad"),
        )
    with pytest.raises(TypeError, match="effective ID"):
        msgspec.structs.replace(
            application,
            effective_id=cast("EffectiveRuleId", "bad"),
        )
    with pytest.raises(TypeError, match="fingerprint"):
        msgspec.structs.replace(
            application,
            rule_fingerprint=cast("Fingerprint", "bad"),
        )
    with pytest.raises(TypeError, match="application pair"):
        msgspec.structs.replace(
            application,
            pair=cast("DirectedEnvironmentPair", "bad"),
        )


@pytest.mark.parametrize(
    ("outcome", "baseline", "candidate"),
    [
        (DiffFactOutcome.ADDED, False, True),
        (DiffFactOutcome.REMOVED, True, False),
        (DiffFactOutcome.SAME, True, True),
        (DiffFactOutcome.CHANGED, True, True),
        (DiffFactOutcome.IGNORED, True, False),
    ],
)
def test_fact_directionality(
    outcome: DiffFactOutcome,
    baseline: bool,
    candidate: bool,
) -> None:
    fact = DiffFact(
        fact_id=FactId(value=UUID(int=64)),
        outcome=outcome,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline_location=_location(65) if baseline else None,
        candidate_location=_location(66) if candidate else None,
    )
    assert (fact.baseline_location is not None, fact.candidate_location is not None) == (
        baseline,
        candidate,
    )


def test_added_and_removed_cannot_reverse_direction() -> None:
    with pytest.raises(ValueError, match="candidate"):
        DiffFact(
            fact_id=FactId(value=UUID(int=67)),
            outcome=DiffFactOutcome.ADDED,
            comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
            baseline_location=_location(68),
        )
    with pytest.raises(ValueError, match="baseline"):
        DiffFact(
            fact_id=FactId(value=UUID(int=69)),
            outcome=DiffFactOutcome.REMOVED,
            comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
            candidate_location=_location(70),
        )


@given(
    same=st.integers(min_value=0, max_value=100),
    changed=st.integers(min_value=0, max_value=100),
    added=st.integers(min_value=0, max_value=100),
    removed=st.integers(min_value=0, max_value=100),
    ignored=st.integers(min_value=0, max_value=100),
    incomparable=st.integers(min_value=0, max_value=100),
)
def test_fact_count_conservation(
    same: int,
    changed: int,
    added: int,
    removed: int,
    ignored: int,
    incomparable: int,
) -> None:
    counts = DiffFactCounts(
        same=Count(value=same),
        changed=Count(value=changed),
        added=Count(value=added),
        removed=Count(value=removed),
        ignored=Count(value=ignored),
        incomparable=Count(value=incomparable),
    )
    assert counts.evaluated == same + changed + added + removed + incomparable
    assert counts.total == counts.evaluated + ignored


@given(
    compared=st.integers(min_value=0, max_value=100),
    ignored=st.integers(min_value=0, max_value=100),
    not_evaluated=st.integers(min_value=0, max_value=100),
)
def test_coverage_conservation(compared: int, ignored: int, not_evaluated: int) -> None:
    paired = compared + ignored + not_evaluated
    coverage = DiffCoverage(
        baseline_selected=Count(value=paired),
        candidate_selected=Count(value=paired),
        baseline_derived=Count(value=paired),
        candidate_derived=Count(value=paired),
        paired=Count(value=paired),
        compared=Count(value=compared),
        ignored=Count(value=ignored),
        not_evaluated=Count(value=not_evaluated),
    )
    assert coverage.paired.value == (
        coverage.compared.value + coverage.ignored.value + coverage.not_evaluated.value
    )


def test_diff_counts_and_coverage_reject_untyped_or_overresolved_values() -> None:
    zero = Count(value=0)
    with pytest.raises(TypeError, match="Count"):
        DiffFactCounts(
            same=cast("Count", 0),
            changed=zero,
            added=zero,
            removed=zero,
            ignored=zero,
            incomparable=zero,
        )
    with pytest.raises(TypeError, match="Count"):
        DiffCoverage(
            baseline_selected=zero,
            candidate_selected=zero,
            baseline_derived=zero,
            candidate_derived=zero,
            paired=zero,
            compared=cast("Count", 0),
            ignored=zero,
            not_evaluated=zero,
        )
    with pytest.raises(ValueError, match="paired unit"):
        DiffCoverage(
            baseline_selected=zero,
            candidate_selected=zero,
            baseline_derived=zero,
            candidate_derived=zero,
            paired=zero,
            compared=Count(value=1),
            ignored=zero,
            not_evaluated=zero,
        )
