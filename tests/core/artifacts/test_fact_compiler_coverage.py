"""Exhaustive branch tests for the safe assertion and diff fact projection."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from comparo.core.artifacts import fact_compiler
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.artifacts.values import ArtifactArray
from comparo.core.artifacts.values import ArtifactBoolean
from comparo.core.artifacts.values import ArtifactDecimal
from comparo.core.artifacts.values import ArtifactInteger
from comparo.core.artifacts.values import ArtifactNull
from comparo.core.artifacts.values import ArtifactObject
from comparo.core.artifacts.values import ArtifactString
from comparo.core.domain.assertions.match_results import MatchFact
from comparo.core.domain.assertions.match_results import MatchOutcome
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionErrorScope
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.evidence import DiffFact
from comparo.core.domain.diffs.evidence import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffIncompleteReason
from comparo.core.domain.diffs.results import DiffPipelineIssue
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.locations import AttemptLocation
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import ExecutionLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import HttpMessageSection
from comparo.core.domain.selectors.locations import IndexedLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.locations import ScenarioLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.artifacts.test_capture_compiler import _policy
from tests.core.domain.assertions._invariant_support import _compiled_rule
from tests.core.domain.diffs._invariant_support import _compiled_rule as _compiled_diff_rule
from tests.core.domain.diffs._invariant_support import _location as _diff_location
from tests.core.domain.diffs._invariant_support import _value_provenance
from tests.core.domain.gates.test_normalization import _assertion_fact
from tests.core.domain.gates.test_normalization import _diff_counts
from tests.core.domain.gates.test_normalization import _diff_coverage
from tests.core.domain.gates.test_normalization import _diff_fact
from tests.core.domain.gates.test_normalization import _predicate_rule
from tests.core.domain.gates.test_normalization import _zero_assertion_counts

_ORIGIN = TaintOriginId(value="secret/test/value")


def _boundary(*values: object) -> SanitizationBoundary:
    boundary = SanitizationBoundary(_policy().limits.sanitization)
    for value in values:
        boundary.register(SensitiveValue(value, origins=(_ORIGIN,)))
    boundary.seal()
    return boundary


def test_public_fact_compilers_reject_untyped_inputs() -> None:
    boundary = _boundary()
    with pytest.raises(TypeError, match="typed inputs"):
        fact_compiler.compile_assertion_fact(
            cast("AssertionFact", object()), boundary, retain_evidence=False
        )
    with pytest.raises(TypeError, match="typed inputs"):
        fact_compiler.compile_diff_fact(
            cast("DiffRuleFact", object()), boundary, retain_evidence=False
        )


def test_assertion_failures_are_projected_without_live_diagnostics() -> None:
    unavailable = _assertion_fact(
        AssertionUnavailable(code=DiagnosticCode(value="assertion.unavailable")),
        _zero_assertion_counts(),
    )
    error = _assertion_fact(
        AssertionError(
            code=DiagnosticCode(value="assertion.failed"),
            scope=AssertionErrorScope.EVALUATION,
        ),
        AssertionCounts(
            selected=Count(value=1),
            evaluated=Count(value=1),
            satisfied=Count(value=0),
            violated=Count(value=0),
            errors=Count(value=1),
        ),
        rule=_predicate_rule(),
    )

    unavailable_artifact = fact_compiler.compile_assertion_fact(
        unavailable, _boundary(), retain_evidence=False
    )
    error_artifact = fact_compiler.compile_assertion_fact(error, _boundary(), retain_evidence=False)

    assert unavailable_artifact.issues[0].stage == "assertion"
    assert unavailable_artifact.issues[0].code == "assertion.unavailable"
    assert error_artifact.issues[0].stage == "assertion-evaluation"
    assert error_artifact.issues[0].code == "assertion.failed"


def test_diff_failures_include_specific_issues_or_primary_reason() -> None:
    reason = DiffIncompleteReason(
        side=DiffSide.BOTH,
        stage=DiffEvaluationStage.SELECTION,
        cause=DiffIncompleteCause.UNAVAILABLE,
    )
    without_issues = _diff_fact(
        Incomplete(reason=reason),
        _diff_counts(),
        _diff_coverage(not_evaluated=1),
    )
    issue = DiffPipelineIssue(
        side=DiffSide.BOTH,
        stage=DiffEvaluationStage.SELECTION,
        cause=DiffIncompleteCause.UNAVAILABLE,
        code=DiagnosticCode(value="diff.selection-unavailable"),
    )
    with_issues = _diff_fact(
        Incomplete(reason=reason, issues=(issue,)),
        _diff_counts(),
        _diff_coverage(not_evaluated=1),
    )
    failed = _diff_fact(
        DiffEvaluationError(code=DiagnosticCode(value="diff.failed")),
        _diff_counts(),
        _diff_coverage(),
    )

    fallback = fact_compiler.compile_diff_fact(without_issues, _boundary(), retain_evidence=False)
    specific = fact_compiler.compile_diff_fact(with_issues, _boundary(), retain_evidence=False)
    error = fact_compiler.compile_diff_fact(failed, _boundary(), retain_evidence=False)

    assert (fallback.issues[0].stage, fallback.issues[0].code) == (
        "selection",
        "unavailable",
    )
    assert specific.issues[0].code == "diff.selection-unavailable"
    assert (error.issues[0].stage, error.issues[0].code) == ("diff", "diff.failed")


def test_successful_facts_and_retained_evidence_have_no_issues() -> None:
    assertion = _assertion_fact(Satisfied(), _zero_assertion_counts())
    diff = _diff_fact(
        Equivalent(),
        _diff_counts(same=1),
        _diff_coverage(compared=1),
    )
    assert not fact_compiler._assertion_issues(assertion)
    assert not fact_compiler._diff_issues(diff)

    match = MatchFact(
        fact_id=FactId(value=UUID(int=20)),
        match=SelectionMatch(
            value=JsonInteger(value=200),
            location=_diff_location(20),
        ),
        outcome=MatchOutcome.SATISFIED,
    )
    assertion_evidence = fact_compiler._assertion_evidence(match, _boundary())
    assert assertion_evidence.values[0].name == "actual"

    comparator = _compiled_diff_rule().pipeline.comparator.identity
    baseline = _value_provenance(_diff_location(21))
    candidate = _value_provenance(_diff_location(22, candidate=True))
    removed = DiffFact(
        fact_id=FactId(value=UUID(int=21)),
        outcome=DiffFactOutcome.REMOVED,
        comparator=comparator,
        baseline=baseline,
    )
    added = DiffFact(
        fact_id=FactId(value=UUID(int=22)),
        outcome=DiffFactOutcome.ADDED,
        comparator=comparator,
        candidate=candidate,
    )
    assert (
        str(_diff_location(21).exchange_id.value)
        in fact_compiler._diff_evidence(removed, _boundary()).location
    )
    candidate_exchange_id = str(_diff_location(22, candidate=True).exchange_id.value)
    assert candidate_exchange_id in fact_compiler._diff_evidence(added, _boundary()).location


@pytest.mark.parametrize(
    ("value", "expected_type"),
    [
        (JsonString(value="text"), ArtifactString),
        (JsonInteger(value=7), ArtifactInteger),
        (JsonDecimal(value=Decimal("1.25")), ArtifactDecimal),
        (JsonBoolean(value=True), ArtifactBoolean),
        (JsonNull(), ArtifactNull),
        (JsonArray(items=(JsonInteger(value=1),)), ArtifactArray),
        (
            JsonObject(members=(JsonObjectMember(name="field", value=JsonString(value="value")),)),
            ArtifactObject,
        ),
        (BytesValue(value=b"bytes"), ArtifactString),
        (Duration(nanoseconds=12), ArtifactInteger),
        (UtcInstant(unix_nanoseconds=13), ArtifactInteger),
        (Ratio(numerator=1, denominator=PositiveCount(value=2)), ArtifactString),
        (BareQueryFieldValue(), ArtifactNull),
        (
            HttpFieldItems(items=(MediaTypeTokenValue(value="gzip"),)),
            ArtifactArray,
        ),
        (HttpFieldTextValue(value="header"), ArtifactString),
        (MediaType(type="application", subtype="json"), ArtifactString),
        (Count(value=4), ArtifactInteger),
        (ByteCount(value=5), ArtifactInteger),
        (BodyId(value=UUID(int=6)), ArtifactString),
        (ViewId(value=UUID(int=7)), ArtifactString),
    ],
)
def test_selected_value_projects_every_supported_carrier(
    value: object,
    expected_type: type[object],
) -> None:
    projected = fact_compiler._selected_value(cast("SelectedValue", value), _boundary())
    assert type(projected) is expected_type


def test_selected_value_redacts_sensitive_and_matching_scalar_values() -> None:
    sensitive = SensitiveValue(JsonString(value="secret"), origins=(_ORIGIN,))
    projected_sensitive = fact_compiler._selected_value(sensitive, _boundary())
    assert type(projected_sensitive) is ArtifactString
    assert projected_sensitive.value == "<redacted>"

    redacting = _boundary(JsonInteger(value=7))
    projected_count = fact_compiler._selected_value(Count(value=7), redacting)
    projected_json = fact_compiler._sanitize_json(JsonInteger(value=7), redacting)
    assert type(projected_count) is ArtifactString
    assert type(projected_json) is ArtifactString
    assert projected_count.value == "<redacted>"
    assert projected_json.value == "<redacted>"

    with pytest.raises(TypeError, match="unsupported value"):
        fact_compiler._selected_value(cast("SelectedValue", object()), _boundary())


def test_json_scalar_rendering_covers_numeric_boolean_and_null_forms() -> None:
    assert fact_compiler._json_scalar_text(JsonInteger(value=3)) == "3"
    assert fact_compiler._json_scalar_text(JsonDecimal(value=Decimal("2.5"))) == "2.5"
    assert fact_compiler._json_scalar_text(JsonBoolean(value=True)) == "true"
    assert fact_compiler._json_scalar_text(JsonBoolean(value=False)) == "false"
    assert fact_compiler._json_scalar_text(JsonNull()) == "null"
    with pytest.raises(TypeError, match="unsupported exact value"):
        fact_compiler._sanitize_json(cast("JsonNull", object()), _boundary())


def test_locations_project_every_concrete_coordinate_shape() -> None:
    execution_id = EnvironmentExecutionId(value=UUID(int=1))
    exchange_id = ExchangeId(value=UUID(int=2))
    view_id = ViewId(value=UUID(int=3))
    field = StaticFieldPath(segments=("response", "status"))
    locations = (
        ExecutionLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.EXECUTION,
            field=field,
        ),
        AttemptLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.ATTEMPT,
            attempt_id=AttemptId(value=UUID(int=4)),
            field=field,
        ),
        ExchangeLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_STATUS,
            exchange_id=exchange_id,
            field=field,
        ),
        HeaderLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_HEADERS,
            exchange_id=exchange_id,
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=Count(value=0),
            name=HeaderName(value="etag"),
        ),
        NamedFieldLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.REQUEST_QUERY,
            exchange_id=exchange_id,
            name=QueryName(value="page"),
            occurrence=Count(value=0),
        ),
        NamedFieldLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.REQUEST_COOKIES,
            exchange_id=exchange_id,
            name=CookieName(value="session"),
            occurrence=Count(value=0),
        ),
        IndexedLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_ENCODINGS,
            exchange_id=exchange_id,
            index=Count(value=1),
        ),
        JsonLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_BODY,
            view_id=view_id,
            pointer=JsonPointer(value="/item"),
            body_view=BodyViewKind.JSON,
        ),
        ViewLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_BODY,
            view_id=view_id,
            body_view=BodyViewKind.TEXT,
        ),
        BodyLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=5)),
            body_view=BodyViewKind.HANDLE,
        ),
        BodyLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=6)),
            body_view=BodyViewKind.ENTITY_BYTES,
            start=ByteCount(value=1),
            end=ByteCount(value=3),
        ),
        StreamLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.STREAM_RECORDS,
            view_id=view_id,
            connection_segment=Count(value=0),
            item_index=Count(value=1),
            category_index=Count(value=2),
        ),
        StreamLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.STREAM_EVENTS,
            view_id=ViewId(value=UUID(int=8)),
            connection_segment=Count(value=0),
            item_index=Count(value=1),
            category_index=Count(value=2),
            nested_pointer=JsonPointer(value="/data"),
        ),
        ScenarioLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.SCENARIO_STEP,
            step_execution_id=StepExecutionId(value=UUID(int=9)),
            field=StaticFieldPath(segments=("output",)),
        ),
    )

    projected = tuple(fact_compiler._location(item, _boundary()) for item in locations)

    assert all(value.startswith(str(execution_id.value)) for value in projected)
    assert "[1:3]" in projected[10]
    assert projected[11].endswith("/0/1/2")
    assert projected[12].endswith("/0/1/2/data")


def test_inline_rule_origin_is_projected_without_becoming_a_reference() -> None:
    definition = _compiled_rule().definition
    referenced = fact_compiler._rule_definition(
        definition.origin,
        kind="AssertionRule",
        rule_id="status",
        fingerprint="a" * 64,
    )
    assert referenced.resource_id == "api/status"

    assert hasattr(definition.origin, "identity")
    owner = definition.origin.identity
    inline = fact_compiler._rule_definition(
        InlineDefinitionOrigin(owner=owner, fragment=InlineFragment(value="#assertions")),
        kind="AssertionRule",
        rule_id="status",
        fingerprint="b" * 64,
    )
    assert inline.resource_id == "api#assertions/status"
