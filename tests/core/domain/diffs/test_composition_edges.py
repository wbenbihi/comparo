"""Cover diff operation catalogs, directionality, and aggregate invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import CollectionMap
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import Projection
from comparo.core.domain.diffs.operations import RelativeSelection
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffIncompleteReason
from comparo.core.domain.diffs.results import DiffNotEvaluatedReason
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.sources import SelectorCardinality
from tests.core.domain.diffs._invariant_support import _application
from tests.core.domain.diffs._invariant_support import _compiled_rule
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _object_selector
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _optional_item_selector
from tests.core.domain.diffs._invariant_support import _plural_item_selector
from tests.core.domain.diffs._invariant_support import _plural_selector
from tests.core.domain.diffs._invariant_support import _selector


def test_side_pipeline_derives_cardinality_across_transform_composition() -> None:
    relative_optional = RelativeSelection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "relative-select"),
        selector=_optional_item_selector(),
    )
    relative_exact = RelativeSelection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "relative-select"),
        selector=_item_selector(),
    )
    assert (
        SidePipeline(
            selector=_object_selector(),
            transforms=(relative_optional,),
        ).output_cardinality
        is SelectorCardinality.ZERO_OR_ONE
    )
    assert (
        SidePipeline(
            selector=_selector(),
            transforms=(relative_exact,),
        ).output_cardinality
        is SelectorCardinality.EXACTLY_ONE
    )
    assert (
        SidePipeline(
            selector=_plural_selector(),
            transforms=(relative_exact,),
        ).output_cardinality
        is SelectorCardinality.ZERO_OR_MORE
    )
    assert (
        SidePipeline(
            selector=_object_selector(),
            transforms=(
                Projection(
                    identity=_operation(DiffOperationFamily.TRANSFORM, "project"),
                    pointers=(JsonPointer(value="/id"),),
                ),
            ),
        ).output_cardinality
        is SelectorCardinality.EXACTLY_ONE
    )
    assert (
        SidePipeline(
            selector=_selector(),
            transforms=(
                CollectionMap(
                    identity=_operation(DiffOperationFamily.TRANSFORM, "collection-map"),
                    selector=_plural_item_selector(),
                ),
            ),
        ).output_cardinality
        is SelectorCardinality.ZERO_OR_MORE
    )


def test_typed_aggregate_outcomes_reject_unsupported_reasons_and_precedence() -> None:
    with pytest.raises(TypeError, match="DiffIncompleteReason"):
        Incomplete(reason=cast("DiffIncompleteReason", "bad"))
    with pytest.raises(TypeError, match="DiffNotEvaluatedReason"):
        NotEvaluated(reason=cast("DiffNotEvaluatedReason", "empty-selection"))

    zero = Count(value=0)
    zero_counts = DiffFactCounts(
        same=zero,
        changed=zero,
        added=zero,
        removed=zero,
        ignored=zero,
        incomparable=zero,
    )
    zero_coverage = DiffCoverage(
        baseline_selected=zero,
        candidate_selected=zero,
        baseline_derived=zero,
        candidate_derived=zero,
        paired=zero,
        compared=zero,
        ignored=zero,
        not_evaluated=zero,
    )
    with pytest.raises(ValueError, match="pairing incompleteness"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=980)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Incomplete(
                reason=DiffIncompleteReason(
                    side=DiffSide.BOTH,
                    stage=DiffEvaluationStage.PAIRING,
                    cause=DiffIncompleteCause.PARTIAL,
                )
            ),
            counts=zero_counts,
            coverage=zero_coverage,
            evidence=DiffEvidence(retained=(), omitted=zero),
        )
    incomplete = DiffRuleFact(
        fact_id=FactId(value=UUID(int=981)),
        application=_application(),
        rule=_compiled_rule(),
        outcome=Incomplete(
            reason=DiffIncompleteReason(
                side=DiffSide.CANDIDATE,
                stage=DiffEvaluationStage.CAPTURE,
                cause=DiffIncompleteCause.PARTIAL,
            )
        ),
        counts=zero_counts,
        coverage=zero_coverage,
        evidence=DiffEvidence(retained=(), omitted=zero),
    )
    assert type(incomplete.outcome) is Incomplete

    ignored_counts = DiffFactCounts(
        same=zero,
        changed=zero,
        added=zero,
        removed=zero,
        ignored=Count(value=1),
        incomparable=zero,
    )
    ignored_coverage = DiffCoverage(
        baseline_selected=Count(value=1),
        candidate_selected=Count(value=1),
        baseline_derived=Count(value=1),
        candidate_derived=Count(value=1),
        paired=Count(value=1),
        compared=zero,
        ignored=Count(value=1),
        not_evaluated=zero,
    )
    all_ignored = NotEvaluated(reason=DiffNotEvaluatedReason.ALL_IGNORED)
    with pytest.raises(ValueError, match="at least one"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=982)),
            application=_application(),
            rule=_compiled_rule(DiffSeverity.INFO),
            outcome=all_ignored,
            counts=zero_counts,
            coverage=zero_coverage,
            evidence=DiffEvidence(retained=(), omitted=zero),
        )
    with pytest.raises(ValueError, match="concrete facts"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=983)),
            application=_application(),
            rule=_compiled_rule(DiffSeverity.INFO),
            outcome=NotEvaluated(reason=DiffNotEvaluatedReason.FALSE_APPLICABILITY),
            counts=ignored_counts,
            coverage=ignored_coverage,
            evidence=DiffEvidence(retained=(), omitted=Count(value=1)),
        )
    with pytest.raises(ValueError, match="derived and resolved"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=984)),
            application=_application(),
            rule=_compiled_rule(DiffSeverity.INFO),
            outcome=NotEvaluated(reason=DiffNotEvaluatedReason.EMPTY_SELECTION),
            counts=zero_counts,
            coverage=DiffCoverage(
                baseline_selected=Count(value=1),
                candidate_selected=zero,
                baseline_derived=Count(value=1),
                candidate_derived=zero,
                paired=zero,
                compared=zero,
                ignored=zero,
                not_evaluated=zero,
            ),
            evidence=DiffEvidence(retained=(), omitted=zero),
        )
