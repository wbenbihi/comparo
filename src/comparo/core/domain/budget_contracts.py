"""Closed semantic contracts for every independently budgeted resource."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio


class BudgetResource(DomainStrEnum):
    """Independently configurable resource dimensions."""

    REQUESTS = "requests"
    MATRIX_CELLS = "matrix-cells"
    TRIALS = "trials"
    SCENARIO_STEPS = "scenario-steps"
    ENVIRONMENT_EXECUTIONS = "environment-executions"
    ATTEMPTS = "attempts"
    COMPARISONS = "comparisons"
    QUEUED_WORK = "queued-work"
    CONCURRENCY = "concurrency"
    OPEN_CONNECTIONS = "open-connections"
    REQUEST_BYTES = "request-bytes"
    HEADER_BYTES = "header-bytes"
    HEADER_COUNT = "header-count"
    ENCODED_BODY_BYTES = "encoded-body-bytes"
    ENTITY_BODY_BYTES = "entity-body-bytes"
    DECOMPRESSION_RATIO = "decompression-ratio"
    REDIRECTS = "redirects"
    RETRIES = "retries"
    DEADLINE = "deadline"
    STREAM_RECONNECTS = "stream-reconnects"
    STREAM_EVENTS = "stream-events"
    STREAM_BYTES = "stream-bytes"
    STREAM_DURATION = "stream-duration"
    STREAM_IDLE_DURATION = "stream-idle-duration"
    DECODED_CHARACTERS = "decoded-characters"
    JSON_DEPTH = "json-depth"
    JSON_NODES = "json-nodes"
    JSON_TOKENS = "json-tokens"
    VIEW_INDEX_BYTES = "view-index-bytes"
    SELECTION_WORK = "selection-work"
    SELECTION_MATCHES = "selection-matches"
    SELECTED_RESULT_BYTES = "selected-result-bytes"
    DERIVED_VALUES = "derived-values"
    DERIVED_BYTES = "derived-bytes"
    TRANSFORM_WORK = "transform-work"
    PAIRS = "pairs"
    FACTS = "facts"
    TRIAL_SAMPLES = "trial-samples"
    STATISTICAL_WORK = "statistical-work"
    EVIDENCE_EXAMPLES = "evidence-examples"
    EVIDENCE_BYTES = "evidence-bytes"
    MEMORY_BYTES = "memory-bytes"
    TRANSIENT_STORAGE_BYTES = "transient-storage-bytes"
    STORE_QUOTA_BYTES = "store-quota-bytes"
    ARTIFACT_BODY_BYTES = "artifact-body-bytes"
    ARTIFACT_BLOBS = "artifact-blobs"
    ARTIFACT_BYTES = "artifact-bytes"
    EXTERNAL_INPUT_BYTES = "external-input-bytes"
    EXTERNAL_OUTPUT_BYTES = "external-output-bytes"
    EXTERNAL_WORK = "external-work"


class BudgetScopeKind(DomainStrEnum):
    """The accounting boundary for a resource budget."""

    PLAN = "plan"
    RUN = "run"
    CELL = "cell"
    TRIAL = "trial"
    ENVIRONMENT = "environment"
    EXECUTION = "execution"
    STEP = "step"
    ATTEMPT = "attempt"
    BODY = "body"
    STREAM = "stream"
    COMPARISON = "comparison"
    ARTIFACT = "artifact"
    EXTERNAL_CAPABILITY = "external-capability"


class BudgetStage(DomainStrEnum):
    """The stage responsible for enforcing and reporting a budget."""

    PLANNING = "planning"
    SCHEDULING = "scheduling"
    HTTP = "http"
    CAPTURE = "capture"
    STREAM = "stream"
    DECODING = "decoding"
    SELECTION = "selection"
    EVALUATION = "evaluation"
    EVIDENCE = "evidence"
    STORAGE = "storage"
    ARTIFACT = "artifact"
    EXTERNAL_CAPABILITY = "external-capability"


class BudgetAccountingMode(DomainStrEnum):
    """How usage fields are interpreted for a budget dimension."""

    CUMULATIVE = "cumulative"
    CONCURRENT = "concurrent"


class BudgetExhaustionEffect(DomainStrEnum):
    """The typed consequence of exhaustion at the responsible stage."""

    PLAN_REJECTED = "plan-rejected"
    OBSERVATION_PARTIAL = "observation-partial"
    OBSERVATION_UNAVAILABLE = "observation-unavailable"
    EVALUATION_INCOMPLETE = "evaluation-incomplete"
    EVIDENCE_TRUNCATED = "evidence-truncated"
    ARTIFACT_INCOMPLETE = "artifact-incomplete"
    ARTIFACT_REJECTED = "artifact-rejected"
    EXTERNAL_CAPABILITY_UNAVAILABLE = "external-capability-unavailable"


class BudgetDimensionContract(NamedTuple):
    """Exact unit, placement, accounting, and exhaustion semantics for one resource."""

    amount_type: type[Count] | type[ByteCount] | type[Duration] | type[Ratio]
    placements: frozenset[tuple[BudgetScopeKind, BudgetStage]]
    accounting: BudgetAccountingMode
    exhaustion_effects_by_stage: Mapping[BudgetStage, frozenset[BudgetExhaustionEffect]]

    @property
    def scope_kinds(self) -> frozenset[BudgetScopeKind]:
        """Return every scope participating in an allowed placement."""
        return frozenset(scope for scope, _stage in self.placements)

    @property
    def stages(self) -> frozenset[BudgetStage]:
        """Return every stage participating in an allowed placement."""
        return frozenset(stage for _scope, stage in self.placements)

    @property
    def exhaustion_effects(self) -> frozenset[BudgetExhaustionEffect]:
        """Return the union of all stage-specific exhaustion effects."""
        return frozenset(
            effect for effects in self.exhaustion_effects_by_stage.values() for effect in effects
        )


_PLAN_SCOPES = frozenset({BudgetScopeKind.PLAN, BudgetScopeKind.RUN})
_EXECUTION_SCOPES = frozenset(
    {BudgetScopeKind.RUN, BudgetScopeKind.CELL, BudgetScopeKind.TRIAL, BudgetScopeKind.EXECUTION}
)
_HTTP_SCOPES = frozenset(
    {
        BudgetScopeKind.RUN,
        BudgetScopeKind.ENVIRONMENT,
        BudgetScopeKind.EXECUTION,
        BudgetScopeKind.STEP,
        BudgetScopeKind.ATTEMPT,
        BudgetScopeKind.BODY,
    }
)
_STREAM_SCOPES = frozenset({BudgetScopeKind.RUN, BudgetScopeKind.ATTEMPT, BudgetScopeKind.STREAM})
_EVALUATION_SCOPES = frozenset({BudgetScopeKind.RUN, BudgetScopeKind.COMPARISON})
_STORAGE_SCOPES = frozenset({BudgetScopeKind.RUN, BudgetScopeKind.ARTIFACT})

_PLAN_EFFECT = frozenset({BudgetExhaustionEffect.PLAN_REJECTED})
_OBSERVATION_EFFECTS = frozenset(
    {BudgetExhaustionEffect.OBSERVATION_PARTIAL, BudgetExhaustionEffect.OBSERVATION_UNAVAILABLE}
)
_EVALUATION_EFFECT = frozenset({BudgetExhaustionEffect.EVALUATION_INCOMPLETE})
_EVIDENCE_EFFECT = frozenset({BudgetExhaustionEffect.EVIDENCE_TRUNCATED})
_ARTIFACT_EFFECTS = frozenset(
    {BudgetExhaustionEffect.ARTIFACT_INCOMPLETE, BudgetExhaustionEffect.ARTIFACT_REJECTED}
)
_EXTERNAL_EFFECT = frozenset({BudgetExhaustionEffect.EXTERNAL_CAPABILITY_UNAVAILABLE})


def _contract(
    amount_type: type[Count] | type[ByteCount] | type[Duration] | type[Ratio],
    scopes: frozenset[BudgetScopeKind],
    stages: frozenset[BudgetStage],
    effects: frozenset[BudgetExhaustionEffect],
    *,
    accounting: BudgetAccountingMode = BudgetAccountingMode.CUMULATIVE,
    placements: frozenset[tuple[BudgetScopeKind, BudgetStage]] | None = None,
    stage_effects: Mapping[BudgetStage, frozenset[BudgetExhaustionEffect]] | None = None,
) -> BudgetDimensionContract:
    exact_placements = (
        frozenset((scope, stage) for scope in scopes for stage in stages)
        if placements is None
        else placements
    )
    exact_effects = (
        MappingProxyType(dict.fromkeys(stages, effects))
        if stage_effects is None
        else MappingProxyType(dict(stage_effects))
    )
    return BudgetDimensionContract(amount_type, exact_placements, accounting, exact_effects)


_CONTRACTS = MappingProxyType(
    {
        **{
            resource: _contract(
                Count, _PLAN_SCOPES, frozenset({BudgetStage.PLANNING}), _PLAN_EFFECT
            )
            for resource in {
                BudgetResource.REQUESTS,
                BudgetResource.MATRIX_CELLS,
                BudgetResource.TRIALS,
                BudgetResource.SCENARIO_STEPS,
                BudgetResource.ENVIRONMENT_EXECUTIONS,
                BudgetResource.ATTEMPTS,
                BudgetResource.COMPARISONS,
            }
        },
        **{
            resource: _contract(
                Count,
                _EXECUTION_SCOPES,
                frozenset({BudgetStage.SCHEDULING}),
                _OBSERVATION_EFFECTS,
                accounting=BudgetAccountingMode.CONCURRENT,
            )
            for resource in {
                BudgetResource.QUEUED_WORK,
                BudgetResource.CONCURRENCY,
                BudgetResource.OPEN_CONNECTIONS,
            }
        },
        BudgetResource.REQUEST_BYTES: _contract(
            ByteCount, _HTTP_SCOPES, frozenset({BudgetStage.HTTP}), _OBSERVATION_EFFECTS
        ),
        BudgetResource.HEADER_BYTES: _contract(
            ByteCount,
            _HTTP_SCOPES,
            frozenset({BudgetStage.HTTP, BudgetStage.CAPTURE}),
            _OBSERVATION_EFFECTS,
        ),
        BudgetResource.HEADER_COUNT: _contract(
            Count,
            _HTTP_SCOPES,
            frozenset({BudgetStage.HTTP, BudgetStage.CAPTURE}),
            _OBSERVATION_EFFECTS,
        ),
        BudgetResource.ENCODED_BODY_BYTES: _contract(
            ByteCount,
            frozenset({BudgetScopeKind.RUN, BudgetScopeKind.ATTEMPT, BudgetScopeKind.BODY}),
            frozenset({BudgetStage.CAPTURE}),
            _OBSERVATION_EFFECTS,
        ),
        BudgetResource.ENTITY_BODY_BYTES: _contract(
            ByteCount,
            frozenset({BudgetScopeKind.RUN, BudgetScopeKind.ATTEMPT, BudgetScopeKind.BODY}),
            frozenset({BudgetStage.DECODING}),
            _OBSERVATION_EFFECTS,
        ),
        BudgetResource.DECOMPRESSION_RATIO: _contract(
            Ratio,
            frozenset({BudgetScopeKind.BODY}),
            frozenset({BudgetStage.DECODING}),
            _OBSERVATION_EFFECTS,
        ),
        BudgetResource.REDIRECTS: _contract(
            Count, _HTTP_SCOPES, frozenset({BudgetStage.HTTP}), _OBSERVATION_EFFECTS
        ),
        BudgetResource.RETRIES: _contract(
            Count, _HTTP_SCOPES, frozenset({BudgetStage.HTTP}), _OBSERVATION_EFFECTS
        ),
        BudgetResource.DEADLINE: _contract(
            Duration,
            frozenset(set(_HTTP_SCOPES) | {BudgetScopeKind.EXTERNAL_CAPABILITY}),
            frozenset({BudgetStage.HTTP, BudgetStage.EXTERNAL_CAPABILITY}),
            frozenset(set(_OBSERVATION_EFFECTS) | set(_EXTERNAL_EFFECT)),
            placements=frozenset(
                {(scope, BudgetStage.HTTP) for scope in _HTTP_SCOPES}
                | {
                    (
                        BudgetScopeKind.EXTERNAL_CAPABILITY,
                        BudgetStage.EXTERNAL_CAPABILITY,
                    )
                }
            ),
            stage_effects={
                BudgetStage.HTTP: _OBSERVATION_EFFECTS,
                BudgetStage.EXTERNAL_CAPABILITY: _EXTERNAL_EFFECT,
            },
        ),
        BudgetResource.STREAM_RECONNECTS: _contract(
            Count, _STREAM_SCOPES, frozenset({BudgetStage.STREAM}), _OBSERVATION_EFFECTS
        ),
        BudgetResource.STREAM_EVENTS: _contract(
            Count, _STREAM_SCOPES, frozenset({BudgetStage.STREAM}), _OBSERVATION_EFFECTS
        ),
        BudgetResource.STREAM_BYTES: _contract(
            ByteCount, _STREAM_SCOPES, frozenset({BudgetStage.STREAM}), _OBSERVATION_EFFECTS
        ),
        BudgetResource.STREAM_DURATION: _contract(
            Duration, _STREAM_SCOPES, frozenset({BudgetStage.STREAM}), _OBSERVATION_EFFECTS
        ),
        BudgetResource.STREAM_IDLE_DURATION: _contract(
            Duration, _STREAM_SCOPES, frozenset({BudgetStage.STREAM}), _OBSERVATION_EFFECTS
        ),
        **{
            resource: _contract(
                Count,
                frozenset({BudgetScopeKind.RUN, BudgetScopeKind.BODY}),
                frozenset({BudgetStage.DECODING}),
                _EVALUATION_EFFECT,
            )
            for resource in {
                BudgetResource.DECODED_CHARACTERS,
                BudgetResource.JSON_DEPTH,
                BudgetResource.JSON_NODES,
                BudgetResource.JSON_TOKENS,
            }
        },
        BudgetResource.VIEW_INDEX_BYTES: _contract(
            ByteCount,
            frozenset({BudgetScopeKind.RUN, BudgetScopeKind.BODY}),
            frozenset({BudgetStage.DECODING, BudgetStage.SELECTION}),
            _EVALUATION_EFFECT,
        ),
        BudgetResource.SELECTION_WORK: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.SELECTION}), _EVALUATION_EFFECT
        ),
        BudgetResource.SELECTION_MATCHES: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.SELECTION}), _EVALUATION_EFFECT
        ),
        BudgetResource.SELECTED_RESULT_BYTES: _contract(
            ByteCount, _EVALUATION_SCOPES, frozenset({BudgetStage.SELECTION}), _EVALUATION_EFFECT
        ),
        BudgetResource.DERIVED_VALUES: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.EVALUATION}), _EVALUATION_EFFECT
        ),
        BudgetResource.DERIVED_BYTES: _contract(
            ByteCount, _EVALUATION_SCOPES, frozenset({BudgetStage.EVALUATION}), _EVALUATION_EFFECT
        ),
        BudgetResource.TRANSFORM_WORK: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.EVALUATION}), _EVALUATION_EFFECT
        ),
        BudgetResource.PAIRS: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.EVALUATION}), _EVALUATION_EFFECT
        ),
        BudgetResource.FACTS: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.EVALUATION}), _EVALUATION_EFFECT
        ),
        BudgetResource.TRIAL_SAMPLES: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.EVALUATION}), _EVALUATION_EFFECT
        ),
        BudgetResource.STATISTICAL_WORK: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.EVALUATION}), _EVALUATION_EFFECT
        ),
        BudgetResource.EVIDENCE_EXAMPLES: _contract(
            Count, _EVALUATION_SCOPES, frozenset({BudgetStage.EVIDENCE}), _EVIDENCE_EFFECT
        ),
        BudgetResource.EVIDENCE_BYTES: _contract(
            ByteCount, _EVALUATION_SCOPES, frozenset({BudgetStage.EVIDENCE}), _EVIDENCE_EFFECT
        ),
        BudgetResource.MEMORY_BYTES: _contract(
            ByteCount,
            frozenset(set(_STORAGE_SCOPES) | {BudgetScopeKind.BODY}),
            frozenset({BudgetStage.STORAGE, BudgetStage.DECODING}),
            frozenset(set(_OBSERVATION_EFFECTS) | set(_EVALUATION_EFFECT)),
            accounting=BudgetAccountingMode.CONCURRENT,
            placements=frozenset(
                {(scope, BudgetStage.STORAGE) for scope in _STORAGE_SCOPES}
                | {(BudgetScopeKind.BODY, BudgetStage.DECODING)}
            ),
            stage_effects={
                BudgetStage.STORAGE: _OBSERVATION_EFFECTS,
                BudgetStage.DECODING: _EVALUATION_EFFECT,
            },
        ),
        **{
            resource: _contract(
                ByteCount,
                _STORAGE_SCOPES,
                frozenset({BudgetStage.STORAGE}),
                _OBSERVATION_EFFECTS,
                accounting=BudgetAccountingMode.CONCURRENT,
            )
            for resource in {
                BudgetResource.TRANSIENT_STORAGE_BYTES,
                BudgetResource.STORE_QUOTA_BYTES,
            }
        },
        BudgetResource.ARTIFACT_BODY_BYTES: _contract(
            ByteCount, _STORAGE_SCOPES, frozenset({BudgetStage.ARTIFACT}), _ARTIFACT_EFFECTS
        ),
        BudgetResource.ARTIFACT_BLOBS: _contract(
            Count, _STORAGE_SCOPES, frozenset({BudgetStage.ARTIFACT}), _ARTIFACT_EFFECTS
        ),
        BudgetResource.ARTIFACT_BYTES: _contract(
            ByteCount, _STORAGE_SCOPES, frozenset({BudgetStage.ARTIFACT}), _ARTIFACT_EFFECTS
        ),
        BudgetResource.EXTERNAL_INPUT_BYTES: _contract(
            ByteCount,
            frozenset({BudgetScopeKind.EXTERNAL_CAPABILITY}),
            frozenset({BudgetStage.EXTERNAL_CAPABILITY}),
            _EXTERNAL_EFFECT,
        ),
        BudgetResource.EXTERNAL_OUTPUT_BYTES: _contract(
            ByteCount,
            frozenset({BudgetScopeKind.EXTERNAL_CAPABILITY}),
            frozenset({BudgetStage.EXTERNAL_CAPABILITY}),
            _EXTERNAL_EFFECT,
        ),
        BudgetResource.EXTERNAL_WORK: _contract(
            Count,
            frozenset({BudgetScopeKind.EXTERNAL_CAPABILITY}),
            frozenset({BudgetStage.EXTERNAL_CAPABILITY}),
            _EXTERNAL_EFFECT,
        ),
    }
)

if frozenset(_CONTRACTS) != frozenset(BudgetResource):  # pragma: no cover - import invariant
    raise RuntimeError("every BudgetResource must have exactly one semantic contract")


def budget_dimension_contract(resource: BudgetResource) -> BudgetDimensionContract:
    """Return the exhaustive semantic contract for a typed resource."""
    if type(resource) is not BudgetResource:
        raise TypeError("budget contract lookup requires a BudgetResource")
    return _CONTRACTS[resource]
