"""Configuration-to-kernel evaluation binding and fail-closed preflight tests."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application import evaluation_binding as binding
from comparo.application.digest_validation import DEFAULT_DIGEST_VALIDATION_LIMITS_V1
from comparo.application.digest_validation import prepare_digest_predicate
from comparo.core.assertions.inputs import PreparedOperandError as AssertionOperandError
from comparo.core.assertions.inputs import PreparedOperandReady as AssertionOperandReady
from comparo.core.assertions.inputs import PreparedOperandUnavailable as AssertionOperandUnavailable
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.diffs.inputs import PreparedOperandError as DiffOperandError
from comparo.core.diffs.inputs import PreparedOperandReady as DiffOperandReady
from comparo.core.diffs.inputs import PreparedOperandUnavailable as DiffOperandUnavailable
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import Count
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.provenance import ResolutionSource
from comparo.core.domain.provenance import ResolutionStep
from comparo.core.domain.provenance import ResolutionStepOutcome
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import UnavailableReason
from comparo.core.planning import plan_run
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import ResolutionTarget
from comparo.core.resolution import RunContext
from comparo.core.resolution.control import RunCancellation
from comparo.core.resolution.snapshots import cell_inputs
from comparo.core.runtime import NoEvaluationPreparation
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime import UnavailableExecution
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime import run_prepared
from comparo.core.runtime.models import ComparisonWork
from comparo.core.runtime.models import EvaluatedComparison
from comparo.core.runtime.models import ExecutionRecord
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation
from tests.core.resolution.conftest import snapshots
from tests.core.resolution.test_context import FixedClock
from tests.core.runtime.test_vertical_runtime import _driver


def _limits() -> binding.EvaluationRuleLimits:
    return binding.EvaluationRuleLimits(
        selection_work=1_000,
        selection_matches=1_000,
        selected_result_bytes=10_000,
        transform_work=100_000,
        facts=1_000,
        evidence_examples=100,
        evidence_bytes=10_000,
        derived_values=1_000,
        derived_bytes=10_000,
        pairs=1_000,
    )


def _prepared(
    *,
    assertion: str | None = None,
    diff: str | None = None,
    gate: str | None = None,
    inline: bool = False,
    assertion_topology: bool = False,
    assertion_profile_fields: str = "",
    diff_profile_fields: str = "",
    response_schema: str | None = None,
) -> PreparedRun:
    attachments: list[str] = []
    resources: list[str] = []
    if assertion is not None:
        if inline:
            attachments.append(f"assertions: [{{rules: {assertion}{assertion_profile_fields}}}]")
        else:
            attachments.append("assertions: [{$use: assertions}]")
            resources.append(
                document(
                    "AssertionProfile",
                    "assertions",
                    f"{{rules: {assertion}{assertion_profile_fields}}}",
                )
            )
    if diff is not None:
        if inline:
            attachments.append(f"diffs: [{{rules: {diff}{diff_profile_fields}}}]")
        else:
            attachments.append("diffs: [{$use: diffs}]")
            resources.append(
                document(
                    "DiffProfile",
                    "diffs",
                    f"{{rules: {diff}{diff_profile_fields}}}",
                )
            )
    if gate is not None:
        if inline:
            attachments.append(f"gate: {gate}")
        else:
            attachments.append("gate: {$use: strict}")
            resources.append(document("GateProfile", "strict", gate))
    suffix = ", " + ", ".join(attachments) if attachments else ""
    schema = "" if response_schema is None else f", responseSchema: {response_schema}"
    topology = (
        "environments: [{$use: staging}]"
        if assertion_topology
        else "baseline: {$use: staging}, candidates: [{$use: prod}]"
    )
    project = compile_project(
        document(
            "Project",
            "demo",
            f"{{defaults: {{subjects: [{{$use: Request/check}}], {topology}{suffix}}}}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document(
            "Request",
            "check",
            f"{{request: {{method: GET, endpoint: /check}}{schema}}}",
        ),
        *resources,
    )
    result = plan_run(project, invocation(), authority())
    assert result.prepared is not None
    return result.prepared


def _context(prepared: PreparedRun) -> RunContext:
    return RunContext(
        prepared=prepared,
        environment_snapshots=snapshots(prepared),
        clock=FixedClock(),
        generator=DeterministicGenerator(
            SensitiveValue(
                BytesValue(value=b"evaluation-binding-seed"),
                origins=(TaintOriginId(value="test/evaluation-seed"),),
            )
        ),
    )


def _bind(prepared: PreparedRun) -> binding.EvaluationBindingResult:
    return binding.bind_configuration_evaluation(prepared, _context(prepared), _limits())


def _codes(result: binding.EvaluationBindingResult) -> tuple[str, ...]:
    return tuple(item.code.value for item in result.diagnostics)


def _configuration_provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def _resolution_provenance(outcome: ResolutionStepOutcome) -> ResolutionProvenance:
    provider = ValueProviderIdentity(namespace="test", name="binding", version="1")
    scope = RunScope(run_id=RunId(value=UUID(int=901)))
    symbol = SymbolPath(segments=("operand",))
    source = ResolutionSource(
        namespace=ValueNamespace.VARIABLES,
        symbol=symbol,
        provider=provider,
        configuration=_configuration_provenance(),
        scope=scope,
    )
    return ResolutionProvenance(
        namespace=ValueNamespace.VARIABLES,
        symbol=symbol,
        provider=provider,
        scope=scope,
        steps=(ResolutionStep(source=source, outcome=outcome),),
    )


class _StaticResolution:
    def __init__(self, outcome: object) -> None:
        self._outcome = outcome

    async def resolve(self, expression: ValueExpression, target: ResolutionTarget) -> object:
        del expression, target
        return self._outcome


def _runtime_records(
    prepared: PreparedRun,
) -> tuple[tuple[ExecutionRecord, ...], ComparisonWork]:
    cancellation = RunCancellation()
    driver, _transport = _driver(prepared, cancellation)
    result = asyncio.run(
        run_prepared(
            prepared,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=RecordingEventSink(),
        )
    )
    return result.executions, result.comparisons[0].work


def test_supported_status_profiles_run_real_kernels_and_strict_gate() -> None:
    prepared = _prepared(
        assertion=(
            "[{id: status-ok, selector: response.status, "
            "operator: {name: comparo.core/equals@1, expected: 200}, severity: blocking, "
            "quantifier: one, completeness: complete}]"
        ),
        diff=(
            "[{id: status-exact, selector: status, comparator: {name: exact}, "
            "severity: blocking, completeness: complete}]"
        ),
        gate="{}",
    )
    outcome = _bind(prepared)
    evaluation = cast("binding.ConfigurationEvaluationPreparation", outcome.preparation)
    cancellation = RunCancellation()
    driver, _transport = _driver(prepared, cancellation)

    result = asyncio.run(
        run_prepared(
            prepared,
            driver=driver,
            evaluation=evaluation,
            cancellation=cancellation,
            clock=FixedClock(),
            events=RecordingEventSink(),
        )
    )

    assert all(len(record.assertions) == 1 for record in result.executions)
    assert all(
        type(item.outcome) is EvaluatedComparison and len(item.outcome.diffs) == 1
        for item in result.comparisons
    )
    assert result.gate.result.verdict is GateVerdict.PASSED
    assert evaluation.gate_profile is not None
    assert evaluation.snapshot_requirements.body_views == ()
    assert not evaluation.snapshot_requirements.stream


@pytest.mark.parametrize("inline", [False, True])
def test_supported_body_and_synthesized_rule_ids_bind(inline: bool) -> None:
    prepared = _prepared(
        assertion="[{selector: body, operator: {name: notEquals, expected: null}}]",
        diff="[{selector: response.body, comparator: ignore}]",
        inline=inline,
    )
    outcome = _bind(prepared)

    assert outcome.diagnostics == ()
    assert outcome.preparation is not None
    assert outcome.preparation.gate_profile is None
    assert outcome.preparation.snapshot_requirements.body_views == (BodyViewKind.JSON,)
    assert not outcome.preparation.snapshot_requirements.stream


def test_no_requested_profiles_is_a_valid_explicit_empty_preparation() -> None:
    outcome = _bind(_prepared())

    assert outcome.preparation is not None
    assert outcome.diagnostics == ()
    assert outcome.rejected == ()
    assert outcome.preparation.snapshot_requirements.body_views == ()
    assert not outcome.preparation.snapshot_requirements.stream


@pytest.mark.parametrize(
    ("assertion", "expected_code", "family", "alias"),
    [
        (
            "[{selector: status, operator: equals}]",
            "evaluation.operator.unsupported",
            "assertion-operator",
            "equals",
        ),
        (
            "[{selector: response.status, operator: matches}]",
            "evaluation.operator.unsupported",
            "assertion-operator",
            "matches",
        ),
        (
            "[{selector: 'header:X-Request-Id', operator: {name: equals, expected: x}}]",
            "evaluation.selector.unsupported",
            "selector",
            None,
        ),
        (
            "[{selector: response.status, operator: {name: equals, expected: 200}, extra: x}]",
            "evaluation.rule.field-unsupported",
            None,
            None,
        ),
        (
            "[{id: INVALID, selector: response.status, operator: {name: equals, expected: 200}}]",
            "evaluation.rule.id-invalid",
            None,
            None,
        ),
    ],
)
def test_assertion_forms_fail_closed(
    assertion: str,
    expected_code: str,
    family: str | None,
    alias: str | None,
) -> None:
    outcome = _bind(_prepared(assertion=assertion))

    assert expected_code in _codes(outcome)
    assert outcome.preparation is None
    if family is not None:
        rejected = next(item for item in outcome.rejected if item.family.value == family)
        assert rejected.alias == alias


def test_invalid_diff_capability_arguments_are_rejected_before_runtime() -> None:
    outcome = _bind(_prepared(diff="[{selector: status, comparator: numericTolerance}]"))

    assert outcome.preparation is None
    assert outcome.rejected[0].family.value == "diff-comparator"
    assert outcome.rejected[0].alias == "numericTolerance"


@pytest.mark.parametrize(
    "diff",
    [
        "[{selector: status, comparator: exact, transforms: [jsonShape]}]",
        "[{selector: status, comparator: exact, reducer: count}]",
        "[{selector: status, comparator: exact, pairing: ordered}]",
    ],
)
def test_deterministic_diff_pipeline_stages_are_admitted(diff: str) -> None:
    outcome = _bind(_prepared(diff=diff))

    assert outcome.preparation is not None
    assert outcome.diagnostics == ()


def test_typed_selectors_and_deterministic_assertion_catalog_are_admitted() -> None:
    rules = (
        "[{id: status-set, name: Expected status, description: Accepted response, "
        "tags: [contract, http], selector: response.status, "
        "operator: {name: in, choices: [200, 204]}}, "
        "{id: status-range, selector: status, "
        "operator: {name: between, lower: 200, upper: 299}}, "
        "{id: message-prefix, selector: {source: response.body, view: json, path: /message}, "
        "operator: {name: startsWith, expected: hello}}, "
        "{id: at-least-one, selector: {source: response.headers, "
        "field: {name: x-request-id, occurrence: 0}}, "
        "operator: {name: cardinality, minimum: 0}}]"
    )
    outcome = _bind(_prepared(assertion=rules))

    preparation = outcome.preparation
    assert preparation is not None
    compiled = preparation._subjects[0].assertions
    assert len(compiled) == 4
    assert compiled[0].metadata.name == "Expected status"
    assert compiled[0].metadata.tags == ("contract", "http")
    assert compiled[2].selector.body_view is BodyViewKind.JSON


def test_re2_assertions_are_precompiled_and_bound_to_selected_values() -> None:
    prepared = _prepared(
        assertion=(
            "[{id: message-pattern, selector: {source: response.body, view: json, "
            "path: /message}, operator: {name: matches, pattern: '^hello', mode: search}}]"
        )
    )
    outcome = _bind(prepared)
    evaluation = cast("binding.ConfigurationEvaluationPreparation", outcome.preparation)
    rule = evaluation._subjects[0].assertions[0]
    execution_id = EnvironmentExecutionId(value=UUID(int=41_001))
    predicate = evaluation._regexes.predicate(
        cast("RegexOperator", rule.operator),
        SelectionMatch(
            value=JsonString(value="hello from Comparo"),
            location=JsonLocation(
                environment_execution_id=execution_id,
                source=SelectorSource.RESPONSE_BODY,
                view_id=ViewId(value=UUID(int=41_002)),
                pointer=JsonPointer(value="/message"),
                body_view=BodyViewKind.JSON,
            ),
        ),
        execution_id,
        rule.selector.fingerprint,
    )

    assert len(evaluation._regexes.entries) == 1
    assert predicate.outcome is PreparedPredicateOutcome.SATISFIED


def test_invalid_re2_syntax_is_rejected_before_http() -> None:
    outcome = _bind(
        _prepared(
            assertion=(
                "[{id: invalid-pattern, selector: {source: response.body, view: text}, "
                "operator: {name: matches, pattern: '(a)\\1'}}]"
            )
        )
    )

    assert outcome.preparation is None
    assert _codes(outcome) == ("evaluation.operator.unsupported",)


def test_digest_assertion_is_bound_to_exact_bytes_and_expected_operand() -> None:
    expected = hashlib.sha256(b"payload").hexdigest()
    prepared = _prepared(
        assertion=(
            "[{id: payload-digest, selector: {source: response.body, view: entity-bytes}, "
            f"operator: {{name: digestEquals, algorithm: sha256, expected: '{expected}'}}}}]"
        )
    )
    evaluation = cast(
        "binding.ConfigurationEvaluationPreparation",
        _bind(prepared).preparation,
    )
    rule = evaluation._subjects[0].assertions[0]
    operator = cast("DigestOperator", rule.operator)
    execution_id = EnvironmentExecutionId(value=UUID(int=42_001))
    predicate = prepare_digest_predicate(
        operator,
        SelectionMatch(
            value=BytesValue(value=b"payload"),
            location=BodyLocation(
                environment_execution_id=execution_id,
                source=SelectorSource.RESPONSE_BODY,
                body_id=BodyId(value=UUID(int=42_002)),
                body_view=BodyViewKind.ENTITY_BYTES,
            ),
        ),
        AssertionOperandReady(value=JsonString(value=expected)),
        execution_id,
        rule.selector.fingerprint,
        DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
    )

    assert predicate.outcome is PreparedPredicateOutcome.SATISFIED


def test_semantic_diff_exclusion_and_collection_modes_are_admitted() -> None:
    diff = (
        "[{id: body-without-env-id, selector: {source: response.body, view: json}, "
        "transforms: [{name: exclude, pointers: [/id, /createdAt]}], comparator: exact}, "
        "{id: request-ids, selector: {source: response.headers, field: x-request-id}, "
        "pairing: set, comparator: exact}]"
    )
    outcome = _bind(_prepared(diff=diff))

    preparation = outcome.preparation
    assert preparation is not None
    compiled = preparation._subjects[0].diffs
    assert len(compiled) == 2
    assert len(compiled[0].pipeline.baseline.transforms) == 1
    assert compiled[1].pipeline.pairing.identity.name == "set"


def test_advanced_relative_diff_stages_and_keyed_pairing_are_admitted() -> None:
    diff = (
        "[{id: keyed-items, selector: {source: response.body, view: json, path: '$[*]'}, "
        "transforms: [{name: collectionFilter, path: /enabled, "
        "predicate: {name: equals, expected: true}}, "
        "{name: collectionMap, path: ''}, {name: sortBy, path: /id}], "
        "pairing: {name: keyed, baselinePath: /id, candidatePath: /id, "
        "duplicateKeys: incomparable}, comparator: exact}, "
        "{id: grouped, selector: {source: response.body, view: json, path: '$[*]'}, "
        "reducer: {name: groupBy, path: /kind, empty: return-empty-collection, "
        "duplicateKeys: collect}, comparator: exact}, "
        "{id: indexed, selector: {source: response.body, view: json, path: '$[*]'}, "
        "reducer: {name: objectFromEntries, keyPath: /id, valuePath: /value, "
        "duplicateKeys: keep-last}, comparator: exact}]"
    )
    outcome = _bind(_prepared(diff=diff))

    preparation = outcome.preparation
    assert preparation is not None, outcome.diagnostics
    compiled = preparation._subjects[0].diffs
    assert tuple(rule.pipeline.pairing.identity.name for rule in compiled) == (
        "keyed",
        "singular",
        "singular",
    )
    assert (
        tuple(
            rule.pipeline.baseline.reducer.identity.name  # type: ignore[union-attr]
            for rule in compiled[1:]
        )
        == ("group-by", "object-from-entries")
    )


def test_diff_rule_supports_explicit_asymmetric_side_pipelines() -> None:
    diff = (
        "[{id: migration, baseline: {selector: {source: response.body, view: json, "
        "path: /legacy}, transforms: [{name: normalizeText, trim: both}]}, "
        "candidate: {selector: {source: response.body, view: json, path: /current}}, "
        "pairing: singular, comparator: exact}]"
    )
    outcome = _bind(_prepared(diff=diff))

    preparation = outcome.preparation
    assert preparation is not None, outcome.diagnostics
    pipeline = preparation._subjects[0].diffs[0].pipeline
    assert pipeline.baseline.selector.fingerprint != pipeline.candidate.selector.fingerprint
    assert len(pipeline.baseline.transforms) == 1
    assert pipeline.candidate.transforms == ()


def test_assertion_and_diff_profile_includes_compose_before_local_rules() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}], assertions: [{$use: assertion-child}], "
            "diffs: [{$use: diff-child}]}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
        document(
            "AssertionProfile",
            "assertion-base",
            "{rules: [{id: status-ok, selector: status, operator: {name: equals, expected: 200}}]}",
        ),
        document(
            "AssertionProfile",
            "assertion-child",
            "{include: [{$use: assertion-base}], rules: [{id: status-type, "
            "selector: status, operator: {name: isType, expected: integer}}]}",
        ),
        document(
            "DiffProfile",
            "diff-base",
            "{rules: [{id: status-exact, selector: status, comparator: exact}]}",
        ),
        document(
            "DiffProfile",
            "diff-child",
            "{include: [{$use: diff-base}], rules: [{id: body-shape, selector: body, "
            "transforms: [jsonShape], comparator: exact}]}",
        ),
    )
    planned = plan_run(project, invocation(), authority())
    assert planned.prepared is not None
    outcome = _bind(planned.prepared)

    preparation = outcome.preparation
    assert preparation is not None, outcome.diagnostics
    assert tuple(rule.effective_id.value for rule in preparation._subjects[0].assertions) == (
        "status-ok",
        "status-type",
    )
    assert tuple(rule.effective_id.value for rule in preparation._subjects[0].diffs) == (
        "status-exact",
        "body-shape",
    )


def test_empty_profile_and_nonempty_gate_fail_closed() -> None:
    empty = _bind(_prepared(assertion="[]"))
    gate = _bind(_prepared(gate="{thresholds: invalid}"))

    assert _codes(empty) == ("evaluation.profile.empty",)
    assert _codes(gate) == ("evaluation.gate.unsupported",)
    assert gate.rejected[0].family is binding.EvaluationCapabilityFamily.GATE


def test_gate_profile_compiles_explicit_dimensions_and_thresholds() -> None:
    threshold = (
        "{severity: warning, violatedRules: 2, driftedRules: 3, unavailableRules: 4, "
        "incomparableRules: 5, incompleteRules: 6, errorRules: 7}"
    )
    outcome = _bind(
        _prepared(
            gate=(
                "{severities: [warning], environmentRoles: [candidate], "
                "requireCompleteRun: false, requireAssertionCoverage: false, "
                f"requireDiffCoverage: false, thresholds: [{threshold}]}}"
            )
        )
    )

    preparation = outcome.preparation
    assert preparation is not None
    gate = preparation.gate_profile
    assert gate is not None
    assert tuple(item.value for item in gate.severities) == ("warning",)
    assert tuple(item.value for item in gate.environment_roles) == ("candidate",)
    assert not gate.require_complete_run
    assert gate.thresholds[0].violated_rules.value == 2
    assert gate.thresholds[0].error_rules.value == 7


def test_inline_response_schema_compiles_as_an_implicit_assertion() -> None:
    outcome = _bind(
        _prepared(
            response_schema="{type: object, required: [ok], properties: {ok: {type: boolean}}}"
        )
    )

    preparation = outcome.preparation
    assert preparation is not None
    rules = preparation._subjects[0].assertions
    assert tuple(rule.effective_id.value for rule in rules) == ("comparo-response-schema",)
    assert rules[0].operator_id.kind.value == "conforms-to"
    assert rules[0].selector.body_view is BodyViewKind.JSON
    assert len(preparation._schemas.entries) == 1


def test_invalid_and_remote_response_schemas_fail_closed_before_http() -> None:
    invalid = _bind(_prepared(response_schema="{type: unsupported}"))
    remote = _bind(
        _prepared(response_schema="{$use: 'https://schemas.example.test/response.json'}")
    )

    assert _codes(invalid) == ("evaluation.schema.invalid",)
    assert _codes(remote) == ("evaluation.schema.invalid",)
    assert invalid.rejected[0].alias == "conformsTo"


def test_assertion_topology_does_not_require_impossible_diff_coverage() -> None:
    outcome = _bind(
        _prepared(
            assertion="[{selector: status, operator: {name: equals, expected: 200}}]",
            diff="[{selector: status, comparator: exact}]",
            gate="{}",
            assertion_topology=True,
        )
    )

    assert outcome.diagnostics == ()
    assert outcome.preparation is not None
    assert outcome.preparation.gate_profile is not None
    assert outcome.preparation.gate_profile.require_assertion_coverage
    assert not outcome.preparation.gate_profile.require_diff_coverage


def test_inline_gate_is_explicitly_rejected() -> None:
    outcome = _bind(_prepared(gate="{}", inline=True))

    assert _codes(outcome) == ("evaluation.gate.inline-unsupported",)


def test_duplicate_effective_ids_fail_closed() -> None:
    prepared = _prepared(
        assertion=(
            "[{id: duplicate, selector: status, operator: {name: equals, expected: 200}}, "
            "{id: duplicate, selector: status, operator: {name: equals, expected: 201}}]"
        )
    )
    assert "evaluation.rule.id-duplicate" in _codes(_bind(prepared))


@pytest.mark.parametrize("field", binding.EvaluationRuleLimits.__struct_fields__)
def test_evaluation_limits_require_positive_exact_integers(field: str) -> None:
    values = dict.fromkeys(binding.EvaluationRuleLimits.__struct_fields__, 1)
    values[field] = 0
    with pytest.raises(ValueError, match="positive"):
        binding.EvaluationRuleLimits(**values)


def test_binding_public_boundaries_reject_wrong_collaborators() -> None:
    prepared = _prepared()
    context = _context(prepared)
    with pytest.raises(TypeError, match="PreparedRun"):
        binding.bind_configuration_evaluation("bad", context, _limits())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="EvaluationRuleLimits"):
        binding.bind_configuration_evaluation(prepared, context, "bad")  # type: ignore[arg-type]


def test_rejected_capability_invariants_are_fail_closed() -> None:
    resource = _prepared().project.project.definition.identity
    with pytest.raises(TypeError, match="family"):
        binding.RejectedEvaluationCapability(
            family=cast("binding.EvaluationCapabilityFamily", "selector"),
            alias=None,
            resource=resource,
        )
    with pytest.raises(ValueError, match="alias"):
        binding.RejectedEvaluationCapability(
            family=binding.EvaluationCapabilityFamily.SELECTOR,
            alias="x" * 65,
            resource=resource,
        )
    with pytest.raises(TypeError, match="ResourceIdentity"):
        binding.RejectedEvaluationCapability(
            family=binding.EvaluationCapabilityFamily.SELECTOR,
            alias=None,
            resource=cast("ResourceIdentity", "resource"),
        )
    with pytest.raises(TypeError, match="rule index"):
        binding.RejectedEvaluationCapability(
            family=binding.EvaluationCapabilityFamily.SELECTOR,
            alias=None,
            resource=resource,
            rule_index=cast("Count", 1),
        )


def test_binding_result_invariants_keep_success_and_failure_disjoint() -> None:
    prepared = _prepared()
    successful = _bind(prepared)
    preparation = cast("binding.ConfigurationEvaluationPreparation", successful.preparation)
    failed = _bind(_prepared(assertion="[]"))
    diagnostic = failed.diagnostics[0]
    rejected = binding.RejectedEvaluationCapability(
        family=binding.EvaluationCapabilityFamily.SELECTOR,
        alias=None,
        resource=prepared.project.project.definition.identity,
    )

    with pytest.raises(TypeError, match="typed preparation"):
        binding.EvaluationBindingResult(
            preparation=cast("binding.ConfigurationEvaluationPreparation", object())
        )
    with pytest.raises(TypeError, match="diagnostics"):
        binding.EvaluationBindingResult(diagnostics=cast("tuple[Diagnostic, ...]", ("bad",)))
    with pytest.raises(TypeError, match="capabilities"):
        binding.EvaluationBindingResult(
            diagnostics=(diagnostic,),
            rejected=cast("tuple[binding.RejectedEvaluationCapability, ...]", ("bad",)),
        )
    with pytest.raises(ValueError, match="exactly"):
        binding.EvaluationBindingResult()
    with pytest.raises(ValueError, match="exactly"):
        binding.EvaluationBindingResult(preparation=preparation, diagnostics=(diagnostic,))
    with pytest.raises(ValueError, match="cannot reject"):
        binding.EvaluationBindingResult(preparation=preparation, rejected=(rejected,))


def test_evaluation_preparation_constructor_invariants() -> None:
    prepared = _prepared()
    context = _context(prepared)
    valid = cast("binding.ConfigurationEvaluationPreparation", _bind(prepared).preparation)
    limits = _limits()
    subjects = valid._subjects
    schemas = valid._schemas
    regexes = valid._regexes
    digest_limits = valid._digest_limits

    with pytest.raises(TypeError, match="PreparedRun and RunContext"):
        binding.ConfigurationEvaluationPreparation(
            prepared=cast("PreparedRun", object()),
            context=context,
            limits=limits,
            subjects=subjects,
            gate=None,
            schemas=schemas,
            regexes=regexes,
            digest_limits=digest_limits,
        )
    with pytest.raises(TypeError, match="PreparedRun and RunContext"):
        binding.ConfigurationEvaluationPreparation(
            prepared=prepared,
            context=cast("RunContext", object()),
            limits=limits,
            subjects=subjects,
            gate=None,
            schemas=schemas,
            regexes=regexes,
            digest_limits=digest_limits,
        )
    with pytest.raises(TypeError, match="EvaluationRuleLimits"):
        binding.ConfigurationEvaluationPreparation(
            prepared=prepared,
            context=context,
            limits=cast("binding.EvaluationRuleLimits", object()),
            subjects=subjects,
            gate=None,
            schemas=schemas,
            regexes=regexes,
            digest_limits=digest_limits,
        )
    with pytest.raises(TypeError, match="subjects"):
        binding.ConfigurationEvaluationPreparation(
            prepared=prepared,
            context=context,
            limits=limits,
            subjects=cast("tuple[binding._SubjectRules, ...]", (object(),)),
            gate=None,
            schemas=schemas,
            regexes=regexes,
            digest_limits=digest_limits,
        )
    with pytest.raises(TypeError, match="gate"):
        binding.ConfigurationEvaluationPreparation(
            prepared=prepared,
            context=context,
            limits=limits,
            subjects=subjects,
            gate=cast("CompiledGateProfile", object()),
            schemas=schemas,
            regexes=regexes,
            digest_limits=digest_limits,
        )

    mismatched = _context(prepared)
    object.__setattr__(mismatched, "_run", RunScope(run_id=RunId(value=UUID(int=999))))
    with pytest.raises(ValueError, match="another run"):
        binding.ConfigurationEvaluationPreparation(
            prepared=prepared,
            context=mismatched,
            limits=limits,
            subjects=subjects,
            gate=None,
            schemas=schemas,
            regexes=regexes,
            digest_limits=digest_limits,
        )


def test_evaluation_preparation_rejects_wrong_runtime_coordinates() -> None:
    prepared = _prepared(
        assertion="[{selector: status, operator: {name: equals, expected: 200}}]",
        diff="[{selector: status, comparator: exact}]",
    )
    evaluation = cast("binding.ConfigurationEvaluationPreparation", _bind(prepared).preparation)
    records, comparison_value = _runtime_records(prepared)
    baseline = next(
        record
        for record in records
        if record.work.scope.execution_id == comparison_value.baseline_execution_id
    )
    candidate = next(
        record
        for record in records
        if record.work.scope.execution_id == comparison_value.candidate_execution_id
    )
    unavailable = msgspec.structs.replace(
        baseline,
        outcome=UnavailableExecution(
            stage=DiagnosticStage.TRANSPORT,
            code=DiagnosticCode(value="test.unavailable"),
        ),
    )

    with pytest.raises(TypeError, match="observed ExecutionRecord"):
        asyncio.run(evaluation.assertions(prepared, cast("ExecutionRecord", object())))
    with pytest.raises(TypeError, match="observed ExecutionRecord"):
        asyncio.run(evaluation.assertions(prepared, unavailable))
    with pytest.raises(TypeError, match="ComparisonWork"):
        asyncio.run(
            evaluation.diffs(
                prepared,
                cast("ComparisonWork", object()),
                baseline,
                candidate,
            )
        )
    with pytest.raises(TypeError, match="ExecutionRecord sides"):
        asyncio.run(
            evaluation.diffs(
                prepared,
                comparison_value,
                cast("ExecutionRecord", object()),
                candidate,
            )
        )
    with pytest.raises(TypeError, match="ExecutionRecord sides"):
        asyncio.run(
            evaluation.diffs(
                prepared,
                comparison_value,
                baseline,
                cast("ExecutionRecord", object()),
            )
        )
    with pytest.raises(TypeError, match="observed sides"):
        asyncio.run(evaluation.diffs(prepared, comparison_value, unavailable, candidate))
    with pytest.raises(TypeError, match="observed sides"):
        asyncio.run(evaluation.diffs(prepared, comparison_value, baseline, unavailable))
    with pytest.raises(ValueError, match="another PreparedRun"):
        asyncio.run(evaluation.assertions(_prepared(), baseline))
    with pytest.raises(ValueError, match="subject"):
        evaluation._rules(prepared.project.project.definition)


def test_profile_lookup_and_compiler_edge_contracts() -> None:
    named = _prepared(
        assertion="[{selector: status, operator: {name: equals, expected: 200}}]",
        assertion_profile_fields=", parameters: {code: 200}",
    )
    named_handle = named.effective.subjects[0].profiles.assertions[0]
    assert type(named_handle) is DefinitionRef
    missing_named = msgspec.structs.replace(
        named_handle,
        fingerprint=binding._fingerprint("missing"),
    )
    problems = binding._Problems()
    assert binding._profile(named.project, missing_named, problems) is None

    inline = _prepared(
        assertion="[{selector: status, operator: {name: equals, expected: 200}}]",
        inline=True,
    )
    inline_handle = inline.effective.subjects[0].profiles.assertions[0]
    missing_inline = msgspec.structs.replace(
        inline_handle,
        fingerprint=binding._fingerprint("missing-inline"),
    )
    assert binding._profile(inline.project, missing_inline, problems) is None
    tuple(binding._inline_resources(inline.project))

    assert binding._compile_assertion_profile(None, problems) == ()
    assert binding._compile_diff_profile(None, problems) == ()
    profile = named.project.find(named_handle.identity.kind, named_handle.identity.resource_id)
    assert profile is not None
    assert binding._compile_assertion_profile(profile, problems) == ()
    assert binding._compile_diff_profile(profile, problems) == ()


@pytest.mark.parametrize(
    "assertion",
    [
        "[{selector: status, operator: {name: equals, expected: 200}, severity: invalid}]",
        "[{id: [invalid], selector: status, operator: {name: equals, expected: 200}}]",
        "[{operator: {name: equals, expected: 200}}]",
    ],
)
def test_additional_assertion_shapes_fail_offline(assertion: str) -> None:
    outcome = _bind(_prepared(assertion=assertion))

    assert outcome.preparation is None
    assert outcome.diagnostics


@pytest.mark.parametrize(
    "diff",
    [
        "[{selector: status, comparator: {name: exact, unexpected: true}}]",
        "[{id: [invalid], selector: status, comparator: exact}]",
        "[{selector: unsupported, comparator: exact}]",
        "[{selector: status, comparator: exact, severity: invalid}]",
    ],
)
def test_additional_diff_shapes_fail_offline(diff: str) -> None:
    outcome = _bind(_prepared(diff=diff))

    assert outcome.preparation is None
    assert outcome.diagnostics


def test_gate_preflight_rejects_multiple_and_missing_profiles() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/one}, {$use: Request/two}], "
            "baseline: {$use: staging}, candidates: [{$use: prod}], "
            "gate: {$use: gate-one}}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document(
            "Request",
            "one",
            "{request: {method: GET, endpoint: /one}}",
        ),
        document(
            "Request",
            "two",
            "{request: {method: GET, endpoint: /two}}",
        ),
        document("GateProfile", "gate-one", "{}"),
        document("GateProfile", "gate-two", "{}"),
    )
    planning = plan_run(project, invocation(), authority())
    assert planning.prepared is not None
    gate_two = next(
        item.definition
        for item in project.resources
        if item.definition.identity.resource_id.value == "gate-two"
    )
    second = planning.prepared.effective.subjects[1]
    second = msgspec.structs.replace(
        second,
        profiles=msgspec.structs.replace(second.profiles, gate=gate_two),
    )
    multiple_prepared = msgspec.structs.replace(
        planning.prepared,
        effective=msgspec.structs.replace(
            planning.prepared.effective,
            subjects=(planning.prepared.effective.subjects[0], second),
        ),
    )
    multiple = _bind(multiple_prepared)
    assert _codes(multiple) == ("evaluation.gate.multiple",)

    prepared = _prepared(gate="{}")
    handle = prepared.effective.subjects[0].profiles.gate
    assert type(handle) is DefinitionRef
    missing = msgspec.structs.replace(handle, fingerprint=binding._fingerprint("missing-gate"))
    profiles = msgspec.structs.replace(prepared.effective.subjects[0].profiles, gate=missing)
    subject = msgspec.structs.replace(prepared.effective.subjects[0], profiles=profiles)
    altered = msgspec.structs.replace(
        prepared,
        effective=msgspec.structs.replace(prepared.effective, subjects=(subject,)),
    )
    problems = binding._Problems()
    assert binding._compile_gate(altered, (), problems) is None
    assert tuple(item.code.value for item in problems.diagnostics) == (
        "evaluation.profile.inconsistent",
    )
    assert _bind(prepared).preparation is not None


def test_lowering_helpers_reject_frontend_impossible_shapes() -> None:
    prepared = _prepared(assertion="[{selector: status, operator: {name: equals, expected: 200}}]")
    handle = prepared.effective.subjects[0].profiles.assertions[0]
    assert type(handle) is DefinitionRef
    profile = prepared.project.find(handle.identity.kind, handle.identity.resource_id)
    assert profile is not None
    literal = LiteralExpr(
        value=JsonString(value="invalid-rule"),
        provenance=_configuration_provenance(),
    )
    problems = binding._Problems()

    assert binding._compile_assertion_rule(profile, literal, 0, problems) is None
    assert binding._compile_diff_rule(profile, literal, 0, problems) is None
    assert binding._safe_alias(None) is None
    assert _codes(_bind(_prepared(diff="[]"))) == ("evaluation.profile.empty",)


def test_private_profile_and_duplicate_guards_reject_inconsistent_graphs() -> None:
    prepared = _prepared(
        assertion="[{id: same, selector: status, operator: {name: equals, expected: 200}}]"
    )
    evaluation = cast("binding.ConfigurationEvaluationPreparation", _bind(prepared).preparation)
    rule = evaluation._subjects[0].assertions[0]
    named_handle = prepared.effective.subjects[0].profiles.assertions[0]
    assert type(named_handle) is DefinitionRef
    missing = msgspec.structs.replace(named_handle, fingerprint=binding._fingerprint("missing"))
    problems = binding._Problems()
    binding._require_unique_rules((rule, rule), problems, prepared.project, (missing,))
    assert "evaluation.profile.inconsistent" in tuple(
        item.code.value for item in problems.diagnostics
    )

    profile = prepared.project.find(named_handle.identity.kind, named_handle.identity.resource_id)
    assert profile is not None
    invalid = msgspec.structs.replace(profile, spec=JsonString(value="not-an-object"))
    with pytest.raises(TypeError, match="CanonicalObject"):
        binding._spec(invalid)


@pytest.mark.parametrize(
    ("outcome", "assertion_type", "diff_type"),
    [
        (
            Present(
                value=JsonString(value="ready"),
                provenance=_resolution_provenance(ResolutionStepOutcome.PRESENT),
            ),
            AssertionOperandReady,
            DiffOperandReady,
        ),
        (
            Unavailable(
                reason=UnavailableReason.NOT_FOUND,
                provenance=_resolution_provenance(ResolutionStepOutcome.UNAVAILABLE),
            ),
            AssertionOperandUnavailable,
            DiffOperandUnavailable,
        ),
        (
            Missing(provenance=_resolution_provenance(ResolutionStepOutcome.MISSING)),
            AssertionOperandUnavailable,
            DiffOperandUnavailable,
        ),
        (
            Error(
                code=ResolutionErrorCode.PROVIDER_FAILED,
                provenance=_resolution_provenance(ResolutionStepOutcome.ERROR),
            ),
            AssertionOperandError,
            DiffOperandError,
        ),
        (
            Blocked(
                code=ResolutionBlockCode.SOURCE_DENIED,
                provenance=_resolution_provenance(ResolutionStepOutcome.BLOCKED),
            ),
            AssertionOperandError,
            DiffOperandError,
        ),
    ],
)
def test_operand_resolution_maps_every_closed_outcome(
    outcome: object,
    assertion_type: type[object],
    diff_type: type[object],
) -> None:
    prepared = _prepared()
    work = next(iter_execution_work(prepared))
    expression = LiteralExpr(
        value=JsonString(value="operand"),
        provenance=_configuration_provenance(),
    )
    target = ResolutionTarget(
        symbol=SymbolPath(segments=("operand",)),
        scope=work.scope,
        cell_inputs=cell_inputs(prepared, work.trial.cell),
    )
    context = cast("RunContext", _StaticResolution(outcome))

    assertion = asyncio.run(binding._assertion_operand(context, expression, target))
    diffs = asyncio.run(
        binding._diff_operands(
            context,
            (expression,),
            "baseline",
            EffectiveRuleId(value="operand"),
            cast("ExecutionRecord", SimpleNamespace(work=work)),
            prepared,
        )
    )

    assert type(assertion) is assertion_type
    assert type(diffs[0].result) is diff_type


def test_operand_resolution_rejects_outcomes_outside_closed_domain() -> None:
    prepared = _prepared()
    work = next(iter_execution_work(prepared))
    expression = LiteralExpr(
        value=JsonString(value="operand"),
        provenance=_configuration_provenance(),
    )
    target = ResolutionTarget(
        symbol=SymbolPath(segments=("operand",)),
        scope=work.scope,
        cell_inputs=cell_inputs(prepared, work.trial.cell),
    )
    context = cast("RunContext", _StaticResolution(object()))

    with pytest.raises(TypeError, match="unsupported outcome"):
        asyncio.run(binding._assertion_operand(context, expression, target))
    with pytest.raises(TypeError, match="unsupported outcome"):
        asyncio.run(
            binding._diff_operands(
                context,
                (expression,),
                "candidate",
                EffectiveRuleId(value="operand"),
                cast("ExecutionRecord", SimpleNamespace(work=work)),
                prepared,
            )
        )
