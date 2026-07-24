"""Compiled directed diff rules and runtime application identity."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.diffs.contracts import derive_side_contract
from comparo.core.domain.diffs.contracts import require_pipeline_contract
from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.operations import is_diff_transform
from comparo.core.domain.diffs.strategies import DiffComparator
from comparo.core.domain.diffs.strategies import DiffPairing
from comparo.core.domain.diffs.strategies import DiffReducer
from comparo.core.domain.diffs.strategies import is_diff_comparator
from comparo.core.domain.diffs.strategies import is_diff_pairing
from comparo.core.domain.diffs.strategies import is_diff_reducer
from comparo.core.domain.diffs.stream_validation import require_stream_projection_source
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.contracts import contract_accepts
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import is_singular_selector
from comparo.core.domain.selectors.sources import SelectorCardinality


class DiffSeverity(DomainStrEnum):
    """Rule importance, independent of facts and gate policy."""

    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class DiffCompletenessRequirement(DomainStrEnum):
    """Minimum source completeness admitted by a pipeline."""

    COMPLETE = "complete"
    CLOSED_WINDOW = "closed-window"
    PARTIAL_PREFIX = "partial-prefix"


class ApplicabilityExpectation(DomainStrEnum):
    """The exact boolean condition required independently on both sides."""

    TRUE = "true"
    FALSE = "false"


@final
class DiffApplicability(msgspec.Struct, frozen=True, kw_only=True):
    """Pure side-specific selectors for explicit rule applicability."""

    baseline_selector: CompiledSelector
    candidate_selector: CompiledSelector
    expectation: ApplicabilityExpectation

    def __post_init__(self) -> None:
        """Require independent typed selectors and explicit expectation."""
        if type(self.baseline_selector) is not CompiledSelector:
            raise TypeError("baseline applicability selector must be a CompiledSelector")
        if type(self.candidate_selector) is not CompiledSelector:
            raise TypeError("candidate applicability selector must be a CompiledSelector")
        if type(self.expectation) is not ApplicabilityExpectation:
            raise TypeError("applicability expectation must be an ApplicabilityExpectation")
        for selector in (self.baseline_selector, self.candidate_selector):
            if not is_singular_selector(selector) or not contract_accepts(
                selector.output,
                frozenset({ValueKind.BOOLEAN}),
            ):
                raise ValueError("diff applicability selectors must be singular booleans")


@final
class SidePipeline(msgspec.Struct, frozen=True, kw_only=True):
    """Independent selection and semantic derivation for one side."""

    selector: CompiledSelector
    transforms: tuple[DiffTransform, ...] = ()
    reducer: DiffReducer | None = None

    def __post_init__(self) -> None:
        """Require immutable typed stages in authored order."""
        if type(self.selector) is not CompiledSelector:
            raise TypeError("side selector must be a CompiledSelector")
        if type(self.transforms) is not tuple:
            raise TypeError("side transforms must be an immutable tuple")
        if any(not is_diff_transform(transform) for transform in self.transforms):
            raise TypeError("side pipeline contains an unsupported transform")
        if self.reducer is not None and not is_diff_reducer(self.reducer):
            raise TypeError("side reducer has an unsupported type")
        require_stream_projection_source(self.selector, self.transforms)
        derive_side_contract(self.selector.output, self.transforms, self.reducer)

    @property
    def output_cardinality(self) -> SelectorCardinality:
        """Derive the strongest static cardinality guarantee after all stages."""
        return self.output_contract.cardinality

    @property
    def output_contract(self) -> ValueContract:
        """Return the fully validated static value/cardinality side output."""
        return derive_side_contract(self.selector.output, self.transforms, self.reducer)


@final
class DiffPipeline(msgspec.Struct, frozen=True, kw_only=True):
    """The complete select, derive, pair, and compare contract."""

    baseline: SidePipeline
    candidate: SidePipeline
    pairing: DiffPairing
    comparator: DiffComparator

    def __post_init__(self) -> None:
        """Require independent sides and typed final stages."""
        if type(self.baseline) is not SidePipeline or type(self.candidate) is not SidePipeline:
            raise TypeError("diff pipeline requires baseline and candidate SidePipeline values")
        if not is_diff_pairing(self.pairing):
            raise TypeError("diff pairing has an unsupported type")
        if not is_diff_comparator(self.comparator):
            raise TypeError("diff comparator has an unsupported type")
        require_pipeline_contract(
            self.baseline.output_contract,
            self.candidate.output_contract,
            self.pairing,
            self.comparator,
        )


@final
class DiffRuleDefinition(msgspec.Struct, frozen=True, kw_only=True):
    """Stable rule identity in a profile or inline executable container."""

    origin: DefinitionRef | InlineDefinitionOrigin
    rule_id: RuleId

    def __post_init__(self) -> None:
        """Require a DiffProfile for reusable definitions."""
        if type(self.origin) not in {DefinitionRef, InlineDefinitionOrigin}:
            raise TypeError("diff rule origin has an unsupported type")
        if (
            type(self.origin) is DefinitionRef
            and self.origin.identity.kind is not ResourceKind.DIFF_PROFILE
        ):
            raise ValueError("reusable diff rule must belong to a DiffProfile")
        if type(self.rule_id) is not RuleId:
            raise TypeError("diff rule ID must be a RuleId")


@final
class CompiledDiffRule(msgspec.Struct, frozen=True, kw_only=True):
    """A statically valid effective diff rule ready for pure evaluation."""

    definition: DiffRuleDefinition
    effective_id: EffectiveRuleId
    fingerprint: Fingerprint
    metadata: Metadata
    severity: DiffSeverity
    completeness: DiffCompletenessRequirement
    pipeline: DiffPipeline
    applicability: DiffApplicability | None = None

    def __post_init__(self) -> None:
        """Reject structurally invalid compiled components."""
        if type(self.definition) is not DiffRuleDefinition:
            raise TypeError("compiled diff definition must be a DiffRuleDefinition")
        if type(self.effective_id) is not EffectiveRuleId:
            raise TypeError("compiled diff effective ID must be an EffectiveRuleId")
        if type(self.fingerprint) is not Fingerprint or type(self.metadata) is not Metadata:
            raise TypeError("compiled diff requires typed fingerprint and metadata")
        if type(self.severity) is not DiffSeverity:
            raise TypeError("compiled diff severity must be a DiffSeverity")
        if type(self.completeness) is not DiffCompletenessRequirement:
            raise TypeError("compiled diff completeness has an unsupported type")
        if type(self.pipeline) is not DiffPipeline:
            raise TypeError("compiled diff pipeline must be a DiffPipeline")
        if self.applicability is not None and type(self.applicability) is not DiffApplicability:
            raise TypeError("compiled diff applicability must be a DiffApplicability")


@final
class DirectedEnvironmentPair(msgspec.Struct, frozen=True, kw_only=True):
    """The non-reversible baseline-to-candidate runtime coordinate."""

    comparison_id: ComparisonId
    baseline_environment: DefinitionRef
    baseline_execution_id: EnvironmentExecutionId
    candidate_environment: DefinitionRef
    candidate_execution_id: EnvironmentExecutionId

    def __post_init__(self) -> None:
        """Require distinct Environment resources and execution occurrences."""
        if type(self.comparison_id) is not ComparisonId:
            raise TypeError("comparison ID must be a ComparisonId")
        for environment in (self.baseline_environment, self.candidate_environment):
            if type(environment) is not DefinitionRef:
                raise TypeError("pair environments must be DefinitionRef values")
            if environment.identity.kind is not ResourceKind.ENVIRONMENT:
                raise ValueError("pair resources must be Environments")
        if self.baseline_environment.identity == self.candidate_environment.identity:
            raise ValueError("directed pair requires distinct Environments")
        if type(self.baseline_execution_id) is not EnvironmentExecutionId:
            raise TypeError("baseline execution ID must be an EnvironmentExecutionId")
        if type(self.candidate_execution_id) is not EnvironmentExecutionId:
            raise TypeError("candidate execution ID must be an EnvironmentExecutionId")
        if self.baseline_execution_id == self.candidate_execution_id:
            raise ValueError("directed pair requires distinct Environment execution IDs")


@final
class DiffApplication(msgspec.Struct, frozen=True, kw_only=True):
    """One effective rule application to a directed Environment pair."""

    application_id: RuleApplicationId
    definition: DiffRuleDefinition
    effective_id: EffectiveRuleId
    rule_fingerprint: Fingerprint
    pair: DirectedEnvironmentPair

    def __post_init__(self) -> None:
        """Require stable definition, effective, and runtime coordinates."""
        if type(self.application_id) is not RuleApplicationId:
            raise TypeError("application ID must be a RuleApplicationId")
        if type(self.definition) is not DiffRuleDefinition:
            raise TypeError("application definition must be a DiffRuleDefinition")
        if type(self.effective_id) is not EffectiveRuleId:
            raise TypeError("application effective ID must be an EffectiveRuleId")
        if type(self.rule_fingerprint) is not Fingerprint:
            raise TypeError("application fingerprint must be a Fingerprint")
        if type(self.pair) is not DirectedEnvironmentPair:
            raise TypeError("application pair must be a DirectedEnvironmentPair")
