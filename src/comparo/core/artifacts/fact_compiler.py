"""Safe assertion and diff fact projections used only by the artifact compiler."""

from __future__ import annotations

from base64 import b64encode

from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactFactEngine
from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.facts import ArtifactEvidence
from comparo.core.artifacts.facts import ArtifactFact
from comparo.core.artifacts.facts import ArtifactNamedValue
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.artifacts.values import ArtifactArray
from comparo.core.artifacts.values import ArtifactBoolean
from comparo.core.artifacts.values import ArtifactDecimal
from comparo.core.artifacts.values import ArtifactInteger
from comparo.core.artifacts.values import ArtifactNull
from comparo.core.artifacts.values import ArtifactObject
from comparo.core.artifacts.values import ArtifactObjectMember
from comparo.core.artifacts.values import ArtifactString
from comparo.core.artifacts.values import ArtifactValue
from comparo.core.domain.assertions.match_results import MatchFact
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.diffs.evidence import DiffFact
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.locations import AttemptLocation
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import ExecutionLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import IndexedLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.locations import ScenarioLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value


def compile_assertion_fact(
    fact: AssertionFact,
    boundary: SanitizationBoundary,
    *,
    retain_evidence: bool,
) -> ArtifactFact:
    """Project one assertion aggregate without retaining a live match or rule object."""
    if type(fact) is not AssertionFact or type(boundary) is not SanitizationBoundary:
        raise TypeError("assertion artifact compilation requires typed inputs")
    evidence = (
        tuple(_assertion_evidence(item, boundary) for item in fact.evidence.retained)
        if retain_evidence
        else ()
    )
    omitted = fact.evidence.omitted.value + (0 if retain_evidence else len(fact.evidence.retained))
    return ArtifactFact(
        fact_id=str(fact.fact_id.value),
        application_id=str(fact.application.application_id.value),
        engine=ArtifactFactEngine.ASSERTION,
        rule=_assertion_rule_definition(fact),
        rule_id=fact.rule.definition.rule_id.value,
        effective_rule_id=fact.rule.effective_id.value,
        severity=ArtifactSeverity(fact.severity.value),
        outcome=_assertion_outcome(fact),
        counts=tuple(
            ArtifactMetric(name=name, value=value)
            for name, value in sorted(
                (
                    ("errors", fact.counts.errors.value),
                    ("evaluated", fact.counts.evaluated.value),
                    ("satisfied", fact.counts.satisfied.value),
                    ("selected", fact.counts.selected.value),
                    ("violated", fact.counts.violated.value),
                )
            )
        ),
        coverage=(
            ArtifactMetric(name="evidence-retained", value=len(evidence)),
            ArtifactMetric(name="evidence-total", value=fact.counts.evaluated.value),
        ),
        evidence=evidence,
        evidence_omitted=omitted,
        issues=_assertion_issues(fact),
    )


def compile_diff_fact(
    fact: DiffRuleFact,
    boundary: SanitizationBoundary,
    *,
    retain_evidence: bool,
) -> ArtifactFact:
    """Project one diff aggregate with locations but no live derived values."""
    if type(fact) is not DiffRuleFact or type(boundary) is not SanitizationBoundary:
        raise TypeError("diff artifact compilation requires typed inputs")
    evidence = (
        tuple(_diff_evidence(item, boundary) for item in fact.evidence.retained)
        if retain_evidence
        else ()
    )
    omitted = fact.evidence.omitted.value + (0 if retain_evidence else len(fact.evidence.retained))
    counts = fact.counts
    coverage = fact.coverage
    return ArtifactFact(
        fact_id=str(fact.fact_id.value),
        application_id=str(fact.application.application_id.value),
        engine=ArtifactFactEngine.DIFF,
        rule=_diff_rule_definition(fact),
        rule_id=fact.rule.definition.rule_id.value,
        effective_rule_id=fact.rule.effective_id.value,
        severity=ArtifactSeverity(fact.severity.value),
        outcome=_diff_outcome(fact),
        counts=tuple(
            ArtifactMetric(name=name, value=value)
            for name, value in sorted(
                (
                    ("added", counts.added.value),
                    ("changed", counts.changed.value),
                    ("ignored", counts.ignored.value),
                    ("incomparable", counts.incomparable.value),
                    ("removed", counts.removed.value),
                    ("same", counts.same.value),
                )
            )
        ),
        coverage=tuple(
            ArtifactMetric(name=name, value=value)
            for name, value in sorted(
                (
                    ("baseline-consumed", coverage.baseline_consumed.value),
                    ("baseline-derived", coverage.baseline_derived.value),
                    ("baseline-resolved", coverage.baseline_resolved.value),
                    ("baseline-selected", coverage.baseline_selected.value),
                    ("candidate-consumed", coverage.candidate_consumed.value),
                    ("candidate-derived", coverage.candidate_derived.value),
                    ("candidate-resolved", coverage.candidate_resolved.value),
                    ("candidate-selected", coverage.candidate_selected.value),
                    ("compared", coverage.compared.value),
                    ("ignored", coverage.ignored.value),
                    ("not-evaluated", coverage.not_evaluated.value),
                    ("paired", coverage.paired.value),
                )
            )
        ),
        evidence=evidence,
        evidence_omitted=omitted,
        issues=_diff_issues(fact),
    )


def _assertion_outcome(fact: AssertionFact) -> ArtifactFactOutcome:
    return {
        Satisfied: ArtifactFactOutcome.SATISFIED,
        Violated: ArtifactFactOutcome.VIOLATED,
        Skipped: ArtifactFactOutcome.SKIPPED,
        AssertionUnavailable: ArtifactFactOutcome.UNAVAILABLE,
        AssertionError: ArtifactFactOutcome.ERROR,
    }[type(fact.outcome)]


def _diff_outcome(fact: DiffRuleFact) -> ArtifactFactOutcome:
    return {
        Equivalent: ArtifactFactOutcome.EQUIVALENT,
        Drifted: ArtifactFactOutcome.DRIFTED,
        Incomplete: ArtifactFactOutcome.INCOMPLETE,
        DiffEvaluationError: ArtifactFactOutcome.ERROR,
        NotEvaluated: ArtifactFactOutcome.NOT_EVALUATED,
    }[type(fact.outcome)]


def _assertion_issues(fact: AssertionFact) -> tuple[ArtifactFailure, ...]:
    outcome = fact.outcome
    if type(outcome) is AssertionUnavailable:
        return (ArtifactFailure(stage="assertion", code=outcome.code.value),)
    if type(outcome) is AssertionError:
        return (ArtifactFailure(stage=f"assertion-{outcome.scope.value}", code=outcome.code.value),)
    return ()


def _diff_issues(fact: DiffRuleFact) -> tuple[ArtifactFailure, ...]:
    outcome = fact.outcome
    if type(outcome) is DiffEvaluationError:
        return (ArtifactFailure(stage="diff", code=outcome.code.value),)
    if type(outcome) is Incomplete:
        return tuple(
            ArtifactFailure(
                stage=item.stage.value,
                code=item.code.value if item.code is not None else item.cause.value,
            )
            for item in outcome.issues
        ) or (ArtifactFailure(stage=outcome.reason.stage.value, code=outcome.reason.cause.value),)
    return ()


def _assertion_evidence(
    fact: MatchFact,
    boundary: SanitizationBoundary,
) -> ArtifactEvidence:
    return ArtifactEvidence(
        fact_id=str(fact.fact_id.value),
        outcome=fact.outcome.value,
        location=_location(fact.match.location, boundary),
        values=(
            ArtifactNamedValue(
                name="actual",
                value=_selected_value(fact.match.value, boundary),
            ),
        ),
    )


def _diff_evidence(fact: DiffFact, boundary: SanitizationBoundary) -> ArtifactEvidence:
    locations = (
        *(fact.baseline.locations if fact.baseline is not None else ()),
        *(fact.candidate.locations if fact.candidate is not None else ()),
    )
    return ArtifactEvidence(
        fact_id=str(fact.fact_id.value),
        outcome=fact.outcome.value,
        location=" | ".join(_location(item, boundary) for item in locations),
    )


def _selected_value(  # noqa: C901
    value: SelectedValue | SensitiveValue[SelectedValue],
    boundary: SanitizationBoundary,
) -> ArtifactValue:
    if is_sensitive_value(value):
        return ArtifactString(value=boundary.sanitize_text(value).value)
    if is_json_value(value):
        return _sanitize_json(value, boundary)
    if type(value) is BytesValue:
        sanitized = boundary.sanitize_bytes(value.value)
        return ArtifactString(value="base64:" + b64encode(sanitized.value).decode("ascii"))
    if type(value) is Duration:
        return ArtifactInteger(value=value.nanoseconds)
    if type(value) is UtcInstant:
        return ArtifactInteger(value=value.unix_nanoseconds)
    if type(value) is Ratio:
        return ArtifactString(value=f"{value.numerator}/{value.denominator.value}")
    if type(value) is BareQueryFieldValue:
        return ArtifactNull()
    if type(value) is HttpFieldItems:
        return ArtifactArray(
            items=tuple(
                ArtifactString(value=boundary.sanitize_text(item.value).value)
                for item in value.items
            )
        )
    if type(value) is HttpFieldTextValue:
        return ArtifactString(value=boundary.sanitize_text(value.value).value)
    if type(value) is MediaType:
        return ArtifactString(value=boundary.sanitize_text(render_media_type(value).value).value)
    if type(value) is Count or type(value) is ByteCount:
        scalar = value.value
        safe = boundary.sanitize_text(str(scalar))
        return (
            ArtifactInteger(value=scalar)
            if not safe.redaction_count
            else ArtifactString(value=safe.value)
        )
    if type(value) is BodyId or type(value) is ViewId:
        return ArtifactString(value=str(value.value))
    raise TypeError("selected artifact evidence contains an unsupported value")


def _sanitize_json(value: JsonValue, boundary: SanitizationBoundary) -> ArtifactValue:
    if type(value) is JsonString:
        return ArtifactString(value=boundary.sanitize_text(value.value).value)
    if type(value) is JsonArray:
        return ArtifactArray(items=tuple(_sanitize_json(item, boundary) for item in value.items))
    if type(value) is JsonObject:
        return ArtifactObject(
            members=tuple(
                ArtifactObjectMember(
                    name=boundary.sanitize_text(item.name).value,
                    value=_sanitize_json(item.value, boundary),
                )
                for item in value.members
            )
        )
    text = _json_scalar_text(value)
    safe = boundary.sanitize_text(text)
    if safe.redaction_count:
        return ArtifactString(value=safe.value)
    if type(value) is JsonInteger:
        return ArtifactInteger(value=value.value)
    if type(value) is JsonDecimal:
        return ArtifactDecimal(value=str(value.value))
    if type(value) is JsonBoolean:
        return ArtifactBoolean(value=value.value)
    if type(value) is JsonNull:
        return ArtifactNull()
    raise TypeError("artifact JSON evidence contains an unsupported exact value")


def _json_scalar_text(value: JsonValue) -> str:
    if type(value) is JsonInteger or type(value) is JsonDecimal:
        return str(value.value)
    if type(value) is JsonBoolean:
        return "true" if value.value else "false"
    return "null"


def _location(  # noqa: C901
    location: ConcreteLocation,
    boundary: SanitizationBoundary,
) -> str:
    owner = str(location.environment_execution_id.value)
    source = location.source.value
    if type(location) is ExecutionLocation:
        suffix = ".".join(location.field.segments)
    elif type(location) is AttemptLocation:
        suffix = f"{location.attempt_id.value}/" + ".".join(location.field.segments)
    elif type(location) is ExchangeLocation:
        suffix = f"{location.exchange_id.value}/" + ".".join(location.field.segments)
    elif type(location) is HeaderLocation:
        name = boundary.sanitize_text(location.name.value).value
        suffix = (
            f"{location.exchange_id.value}/{location.section.value}/"
            f"{name}[{location.occurrence.value}]"
        )
    elif type(location) is NamedFieldLocation:
        name = boundary.sanitize_text(location.name.value).value
        suffix = f"{location.exchange_id.value}/{name}[{location.occurrence.value}]"
    elif type(location) is IndexedLocation:
        suffix = f"{location.exchange_id.value}/[{location.index.value}]"
    elif type(location) is JsonLocation:
        pointer = boundary.sanitize_text(location.pointer.value).value
        suffix = f"{location.view_id.value}#{pointer}"
    elif type(location) is ViewLocation:
        suffix = f"{location.view_id.value}/{location.body_view.value}"
    elif type(location) is BodyLocation:
        if location.start is None:
            bounds = ""
        else:
            end = location.end
            if end is None:  # pragma: no cover - BodyLocation invariant
                raise ValueError("artifact body location is missing its terminal offset")
            bounds = f"[{location.start.value}:{end.value}]"
        suffix = f"{location.body_id.value}/{location.body_view.value}{bounds}"
    elif type(location) is StreamLocation:
        pointer = "" if location.nested_pointer is None else location.nested_pointer.value
        suffix = (
            f"{location.view_id.value}/{location.connection_segment.value}/"
            f"{location.item_index.value}/{location.category_index.value}{pointer}"
        )
    elif type(location) is ScenarioLocation:
        suffix = f"{location.step_execution_id.value}/" + ".".join(location.field.segments)
    else:  # pragma: no cover - closed location union
        raise TypeError("artifact evidence location has an unsupported type")
    return boundary.sanitize_text(f"{owner}/{source}/{suffix}").value


def _assertion_rule_definition(fact: AssertionFact) -> ArtifactDefinition:
    return _rule_definition(
        fact.rule.definition.origin,
        kind="AssertionRule",
        rule_id=fact.rule.definition.rule_id.value,
        fingerprint=fact.rule.fingerprint.digest.hex_value,
    )


def _diff_rule_definition(fact: DiffRuleFact) -> ArtifactDefinition:
    return _rule_definition(
        fact.rule.definition.origin,
        kind="DiffRule",
        rule_id=fact.rule.definition.rule_id.value,
        fingerprint=fact.rule.fingerprint.digest.hex_value,
    )


def _rule_definition(
    origin: DefinitionRef | InlineDefinitionOrigin,
    *,
    kind: str,
    rule_id: str,
    fingerprint: str,
) -> ArtifactDefinition:
    if type(origin) is DefinitionRef:
        namespace = origin.identity.namespace.value
        resource = f"{origin.identity.resource_id.value}/{rule_id}"
    else:
        namespace = origin.owner.namespace.value
        resource = f"{origin.owner.resource_id.value}{origin.fragment.value}/{rule_id}"
    return ArtifactDefinition(
        namespace=namespace,
        kind=kind,
        resource_id=resource,
        fingerprint=fingerprint,
    )
