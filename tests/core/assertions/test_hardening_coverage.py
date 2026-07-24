"""Closed-branch coverage for assertion hardening contracts."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var"

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.bindings import capability_implementation
from comparo.core.assertions.bindings import fingerprints_equal
from comparo.core.assertions.bindings import operands_fingerprint
from comparo.core.assertions.bindings import operation_fingerprint
from comparo.core.assertions.bindings import selected_fingerprint
from comparo.core.assertions.digest import digest_matches
from comparo.core.assertions.digest import validate_digest_expected
from comparo.core.assertions.engine import evaluate_assertion
from comparo.core.assertions.evidence_size import _operator_evidence_size
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedExpression
from comparo.core.assertions.inputs import PreparedOperandReady
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.assertions.inputs import _condition_operator_selectors
from comparo.core.assertions.inputs import _require_binding_fingerprint
from comparo.core.assertions.inputs import require_known_predicate_bindings
from comparo.core.assertions.numeric import _fraction
from comparo.core.assertions.numeric import approximate_result
from comparo.core.assertions.predicates import PredicateSatisfied
from comparo.core.assertions.predicates import PredicateViolated
from comparo.core.assertions.predicates import _seal_result
from comparo.core.assertions.predicates import evaluate_predicate
from comparo.core.assertions.work import charge_evidence_measurement_work
from comparo.core.assertions.work import charge_predicate_work
from comparo.core.domain.assertions.contracts import require_assertion_operator_contract
from comparo.core.domain.assertions.evidence import ApproximateEvidence
from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
from comparo.core.domain.assertions.evidence import SchemaViolationLocation
from comparo.core.domain.assertions.evidence import is_assertion_operator_evidence
from comparo.core.domain.assertions.extended_operators import CompiledSchema
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaRelation
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import ContainsMode
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import KeySetRelation
from comparo.core.domain.assertions.operators import MembershipOperator
from comparo.core.domain.assertions.operators import MembershipRelation
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import MatchFact
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.expressions import ExpressionDependency
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.assertions.test_predicates import _inputs
from tests.core.assertions.test_predicates import _match
from tests.core.assertions.test_predicates import _predicate
from tests.core.domain.assertions._invariant_support import _evidence_exhaustion
from tests.core.domain.assertions.test_contracts import _fingerprint
from tests.core.domain.assertions.test_contracts import _literal
from tests.core.domain.assertions.test_contracts import _regex
from tests.core.domain.assertions.test_contracts import _schema


def _local_schema() -> SchemaOperator:
    identity = ResourceIdentity(
        namespace=NamespaceId(value="demo"),
        kind=ResourceKind.SCHEMA,
        resource_id=ResourceId(value="response"),
    )
    return msgspec.structs.replace(
        _schema(),
        schema=CompiledSchema(
            fingerprint=_fingerprint(),
            resource=DefinitionRef(identity=identity, fingerprint=_fingerprint()),
        ),
        assert_formats=True,
    )


def test_capability_binding_fingerprints_cover_closed_value_domain() -> None:
    parameter = MediaTypeParameter(
        name="note",
        value=MediaTypeQuotedValue(value="hello world"),
    )
    values = (
        JsonNull(),
        BareQueryFieldValue(),
        JsonBoolean(value=True),
        JsonBoolean(value=False),
        JsonInteger(value=-12),
        JsonDecimal(value=Decimal("-1.25")),
        JsonString(value="text"),
        HttpFieldTextValue(value="field"),
        BytesValue(value=b"bytes"),
        JsonArray(items=(JsonInteger(value=1),)),
        JsonObject(members=(JsonObjectMember(name="key", value=JsonString(value="v")),)),
        "plain",
        Count(value=2),
        ByteCount(value=3),
        Duration(nanoseconds=4),
        UtcInstant(unix_nanoseconds=5),
        Ratio(numerator=1, denominator=PositiveCount(value=2)),
        BodyId(value=UUID(int=1)),
        ViewId(value=UUID(int=2)),
        HttpFieldItems(items=(MediaTypeTokenValue(value="gzip"),)),
        MediaType(type="text", subtype="plain", parameters=(parameter,)),
    )
    public = operands_fingerprint(values)
    assert fingerprints_equal(public, operands_fingerprint(values))

    origin = TaintOriginId(value="secret.binding")
    sealed = operands_fingerprint(
        (
            SensitiveValue(JsonString(value="secret"), origins=(origin,)),
            SensitiveValue(JsonString(value="again"), origins=(origin,)),
        )
    )
    assert isinstance(sealed, SensitiveValue)
    assert sealed.origins == (origin,)
    assert fingerprints_equal(sealed, sealed)
    assert selected_fingerprint(_match(JsonString(value="x")))

    with pytest.raises(TypeError, match="prepared-capability"):
        operation_fingerprint(cast("object", object()))
    with pytest.raises(TypeError, match="prepared capability"):
        capability_implementation(cast("object", object()))
    with pytest.raises(TypeError, match="SelectionMatch"):
        selected_fingerprint(cast("object", object()))
    with pytest.raises(TypeError, match="immutable tuple"):
        operands_fingerprint(cast("tuple[object, ...]", []))
    with pytest.raises(TypeError, match="unsupported prepared"):
        operands_fingerprint((object(),))
    with pytest.raises(TypeError, match="must contain"):
        fingerprints_equal(
            cast("object", SensitiveValue(JsonString(value="x"), origins=(origin,))),
            public,
        )
    with pytest.raises(TypeError, match="must be a Fingerprint"):
        fingerprints_equal(cast("object", object()), public)

    assert operation_fingerprint(_local_schema()) != operation_fingerprint(_schema())
    assert capability_implementation(_regex()).identifier.endswith("@1")
    digest = DigestOperator(
        algorithm=DigestAlgorithm.SHA512,
        expected=_literal(BytesValue(value=b"x" * 64)),
    )
    assert capability_implementation(digest).identifier == "comparo.core.digest@1"
    assert operation_fingerprint(digest)


def test_digest_validation_covers_public_sealed_binary_and_hex_inputs() -> None:
    raw = sha256(b"payload").digest()
    operator = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(BytesValue(value=raw)),
    )
    public = DigestEvidence(algorithm=DigestAlgorithm.SHA256, actual=BytesValue(value=raw))
    sealed = DigestEvidence(
        algorithm=DigestAlgorithm.SHA256,
        actual=SensitiveValue(
            BytesValue(value=raw),
            origins=(TaintOriginId(value="secret.digest"),),
        ),
    )
    assert digest_matches(public, BytesValue(value=raw), operator)
    assert digest_matches(sealed, JsonString(value=raw.hex()), operator)
    validate_digest_expected(BytesValue(value=raw), operator)
    validate_digest_expected(JsonString(value=raw.hex()), operator)

    with pytest.raises(ValueError, match="algorithm"):
        digest_matches(
            DigestEvidence(algorithm=DigestAlgorithm.SHA512, actual=BytesValue(value=b"x" * 64)),
            BytesValue(value=raw),
            operator,
        )
    with pytest.raises(ValueError, match="byte width"):
        digest_matches(public, BytesValue(value=b"short"), operator)
    with pytest.raises(ValueError, match="hexadecimal"):
        digest_matches(public, JsonString(value="A" * 64), operator)
    with pytest.raises(TypeError, match="bytes or lowercase"):
        digest_matches(public, JsonInteger(value=1), operator)
    with pytest.raises(ValueError, match="byte width"):
        validate_digest_expected(BytesValue(value=b"short"), operator)
    with pytest.raises(ValueError, match="hexadecimal"):
        validate_digest_expected(JsonString(value="a" * 63), operator)
    with pytest.raises(TypeError, match="bytes or lowercase"):
        validate_digest_expected(JsonInteger(value=1), operator)


def test_work_accounting_covers_closed_values_operators_and_exhaustion_points() -> None:
    location = SchemaViolationLocation(
        instance=JsonPointer(value="/instance"),
        schema=JsonPointer(value="/schema"),
    )
    evidence = SchemaValidationEvidence(
        violation_count=Count(value=1),
        retained=(location,),
    )
    parameter = MediaTypeParameter(name="charset", value=MediaTypeTokenValue(value="utf-8"))
    values = (
        JsonNull(),
        JsonBoolean(value=True),
        BareQueryFieldValue(),
        JsonInteger(value=-12),
        JsonDecimal(value=Decimal("1.25")),
        JsonString(value="text"),
        HttpFieldTextValue(value="field"),
        BytesValue(value=b"bytes"),
        JsonArray(items=(JsonInteger(value=1),)),
        JsonObject(members=(JsonObjectMember(name="long-name", value=JsonNull()),)),
        HttpFieldItems(items=(MediaTypeTokenValue(value="gzip"),)),
        MediaType(type="text", subtype="plain", parameters=(parameter,)),
        Count(value=2),
        ByteCount(value=3),
        Duration(nanoseconds=4),
        UtcInstant(unix_nanoseconds=5),
        Ratio(numerator=1, denominator=PositiveCount(value=2)),
        "plain",
        evidence,
        SensitiveValue(JsonString(value="nested"), origins=(TaintOriginId(value="nested"),)),
    )
    meter = AssertionMeter(control(work=1_000_000).budgets)
    assert charge_evidence_measurement_work(meter, values, evidence) is None
    assert charge_evidence_measurement_work(meter, JsonNull(), None) is None

    contains = ContainsOperator(
        mode=ContainsMode.ALL,
        expected=(_literal(JsonString(value="a")), _literal(JsonString(value="b"))),
    )
    assert (
        charge_predicate_work(
            meter,
            JsonString(value="alphabet"),
            (JsonString(value="a"), JsonString(value="b")),
            contains,
        )
        is None
    )
    membership = MembershipOperator(
        relation=MembershipRelation.IN,
        choices=(_literal(JsonString(value="a")), _literal(JsonString(value="b"))),
    )
    assert (
        charge_predicate_work(
            meter,
            JsonString(value="a"),
            (JsonString(value="a"), JsonString(value="b")),
            membership,
        )
        is None
    )
    string = StringOperator(
        relation=StringRelation.CONTAINS,
        expected=_literal(JsonString(value="needle")),
    )
    assert (
        charge_predicate_work(
            meter,
            HttpFieldTextValue(value="haystack"),
            (HttpFieldTextValue(value="needle"),),
            string,
        )
        is None
    )
    assert (
        charge_predicate_work(
            meter,
            JsonInteger(value=1),
            (JsonInteger(value=1),),
            string,
        )
        is None
    )
    media = MediaTypeOperator(
        relation=MediaRelation.EXACT,
        expected=MediaType(type="text", subtype="plain", parameters=(parameter,)),
    )
    assert charge_predicate_work(meter, values[11], (), media) is None
    assert charge_predicate_work(meter, JsonString(value="x"), (), _schema()) is None
    assert charge_predicate_work(meter, JsonString(value="x"), (), _regex()) is None
    digest = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(BytesValue(value=b"x" * 32)),
    )
    assert (
        charge_predicate_work(
            meter,
            BytesValue(value=b"x"),
            (BytesValue(value=b"x" * 32),),
            digest,
        )
        is None
    )

    assert (
        charge_evidence_measurement_work(
            AssertionMeter(control(work=0).budgets), JsonString(value="x"), None
        )
        is not None
    )
    assert (
        charge_evidence_measurement_work(
            AssertionMeter(control(work=1).budgets), JsonNull(), evidence
        )
        is not None
    )
    assert (
        charge_predicate_work(
            AssertionMeter(control(work=1).budgets),
            JsonNull(),
            (JsonString(value="operand"),),
            membership,
        )
        is not None
    )
    assert (
        charge_predicate_work(
            AssertionMeter(control(work=4).budgets),
            JsonString(value="ab"),
            (JsonString(value="x"), JsonString(value="y")),
            membership,
        )
        is not None
    )
    assert (
        charge_predicate_work(
            AssertionMeter(control(work=20).budgets),
            JsonString(value="a" * 10),
            (JsonString(value="b" * 10),),
            string,
        )
        is not None
    )
    named_object = JsonObject(members=(JsonObjectMember(name="long-name", value=JsonNull()),))
    assert (
        charge_evidence_measurement_work(
            AssertionMeter(control(work=1).budgets), named_object, None
        )
        is not None
    )

    key_set = KeySetOperator(relation=KeySetRelation.HAS, keys=("x",))
    assert charge_predicate_work(meter, JsonObject(members=()), (), key_set) is None
    msgspec.structs.force_setattr(key_set, "keys", ())
    assert charge_predicate_work(meter, JsonObject(members=()), (), key_set) is None
    assert (
        charge_predicate_work(AssertionMeter(control(work=2).budgets), JsonNull(), (), media)
        is not None
    )
    assert (
        charge_predicate_work(AssertionMeter(control(work=12).budgets), JsonNull(), (), media)
        is not None
    )


def test_prepared_input_hardening_defensive_branches() -> None:
    provenance = _literal(JsonString(value="x")).provenance
    lookup = LookupExpr(
        dependency=ExpressionDependency(
            namespace=ValueNamespace.VARIABLES,
            symbol=SymbolPath(segments=("dynamic",)),
            available_at=ValueScopeKind.RUN,
        ),
        provenance=provenance,
    )
    assert (
        type(
            PreparedExpression(
                expression=lookup,
                result=PreparedOperandReady(value=JsonObject(members=())),
            ).result
        )
        is PreparedOperandReady
    )

    match = _match(JsonString(value="x"))
    regex = _regex()
    base = _predicate(regex, match, PreparedCapabilityKind.REGEX)
    with pytest.raises(ValueError, match="provider identity"):
        msgspec.structs.replace(
            base,
            implementation=NamedCapabilityTarget(
                kind=CapabilityTargetKind.SCHEMA_SOURCE,
                identifier="https://example.test/provider",
            ),
        )
    with pytest.raises(ValueError, match="cannot carry"):
        msgspec.structs.replace(
            base,
            outcome=PreparedPredicateOutcome.ERROR,
            code=DiagnosticCode(value="capability.error"),
            evidence=SchemaValidationEvidence(violation_count=Count(value=0)),
        )
    with pytest.raises(ValueError, match="regex predicates"):
        msgspec.structs.replace(
            base,
            evidence=SchemaValidationEvidence(violation_count=Count(value=0)),
        )

    digest = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(BytesValue(value=b"x" * 32)),
    )
    digest_binding = _predicate(
        digest,
        _match(BytesValue(value=b"body")),
        PreparedCapabilityKind.DIGEST,
    )
    with pytest.raises(TypeError, match="digest predicates"):
        msgspec.structs.replace(digest_binding, evidence=None)

    schema = _schema()
    schema_binding = _predicate(schema, match, PreparedCapabilityKind.JSON_SCHEMA)
    with pytest.raises(TypeError, match="schema predicates"):
        msgspec.structs.replace(schema_binding, evidence=None)
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(
            schema_binding,
            outcome=PreparedPredicateOutcome.VIOLATED,
        )
    violated_evidence = SchemaValidationEvidence(
        violation_count=Count(value=1),
        omitted=Count(value=1),
    )
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(schema_binding, evidence=violated_evidence)
    assert (
        msgspec.structs.replace(
            schema_binding,
            outcome=PreparedPredicateOutcome.ERROR,
            code=DiagnosticCode(value="schema.error"),
            evidence=None,
        ).outcome
        is PreparedPredicateOutcome.ERROR
    )

    origin = TaintOriginId(value="secret.selected")
    sensitive_match = msgspec.structs.replace(
        match,
        value=SensitiveValue(JsonString(value="x"), origins=(origin,)),
    )
    sensitive_schema = _predicate(
        schema,
        sensitive_match,
        PreparedCapabilityKind.JSON_SCHEMA,
    )
    with pytest.raises(ValueError, match="taint origins"):
        msgspec.structs.replace(
            sensitive_schema,
            evidence=SensitiveValue(
                SchemaValidationEvidence(violation_count=Count(value=0)),
                origins=(TaintOriginId(value="secret.other"),),
            ),
        )

    with pytest.raises(ValueError, match="input execution"):
        PreparedAssertionInputs(
            rule_fingerprint=rule().fingerprint,
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=999)),
            predicates=(base,),
        )
    with pytest.raises(TypeError, match="compiled rule and inputs"):
        require_known_predicate_bindings(cast("object", object()), inputs())
    with pytest.raises(ValueError, match="foreign"):
        require_known_predicate_bindings(
            rule(),
            msgspec.structs.replace(inputs(), predicates=(base,)),
        )
    with pytest.raises(TypeError, match="unsupported applicability"):
        _condition_operator_selectors(cast("object", object()))
    with pytest.raises(TypeError, match="requires a Fingerprint"):
        _require_binding_fingerprint(
            cast(
                "object",
                SensitiveValue(JsonString(value="x"), origins=(origin,)),
            ),
            "selected",
        )


def test_predicate_capability_defensive_outcomes_and_taint() -> None:
    match = _match(JsonString(value="x"))
    regex = msgspec.structs.replace(_regex(), negate=True)
    satisfied_binding = _predicate(regex, match, PreparedCapabilityKind.REGEX)
    satisfied = evaluate_predicate(
        match,
        regex,
        msgspec.structs.replace(_inputs(regex), predicates=(satisfied_binding,)),
        snapshot(),
    )
    assert type(satisfied) is PredicateViolated
    violated_binding = _predicate(
        regex,
        match,
        PreparedCapabilityKind.REGEX,
        outcome=PreparedPredicateOutcome.VIOLATED,
    )
    violated = evaluate_predicate(
        match,
        regex,
        msgspec.structs.replace(_inputs(regex), predicates=(violated_binding,)),
        snapshot(),
    )
    assert type(violated) is PredicateSatisfied

    digest = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(BytesValue(value=b"x" * 32)),
    )
    digest_match = _match(BytesValue(value=b"body"))
    contradictory = _predicate(
        digest,
        digest_match,
        PreparedCapabilityKind.DIGEST,
        evidence=DigestEvidence(
            algorithm=DigestAlgorithm.SHA256,
            actual=BytesValue(value=b"y" * 32),
        ),
    )
    result = evaluate_predicate(
        digest_match,
        digest,
        msgspec.structs.replace(_inputs(digest), predicates=(contradictory,)),
        snapshot(),
    )
    from comparo.core.assertions.predicates import PredicateError

    assert type(result) is PredicateError

    first = TaintOriginId(value="secret.first")
    second = TaintOriginId(value="secret.second")
    sealed = SensitiveValue(
        ApproximateEvidence(
            delta=Ratio(numerator=0, denominator=PositiveCount(value=1)),
            effective_tolerance=Ratio(numerator=0, denominator=PositiveCount(value=1)),
        ),
        origins=(first,),
    )
    with pytest.raises(ValueError, match="taint differs"):
        _seal_result(PredicateSatisfied(evidence=sealed), (second,))

    schema = _schema()
    schema_binding = _predicate(schema, match, PreparedCapabilityKind.JSON_SCHEMA)
    schema_inputs = msgspec.structs.replace(_inputs(schema), predicates=(schema_binding,))
    msgspec.structs.force_setattr(
        schema_binding,
        "evidence",
        DigestEvidence(
            algorithm=DigestAlgorithm.SHA256,
            actual=BytesValue(value=b"x" * 32),
        ),
    )
    assert type(evaluate_predicate(match, schema, schema_inputs, snapshot())) is PredicateError

    digest_binding = _predicate(
        digest,
        digest_match,
        PreparedCapabilityKind.DIGEST,
    )
    digest_inputs = msgspec.structs.replace(_inputs(digest), predicates=(digest_binding,))
    msgspec.structs.force_setattr(
        digest_binding,
        "evidence",
        SchemaValidationEvidence(violation_count=Count(value=0)),
    )
    assert (
        type(evaluate_predicate(digest_match, digest, digest_inputs, snapshot())) is PredicateError
    )


def test_typed_operator_evidence_validation_and_sizing_branches() -> None:
    ratio = Ratio(numerator=0, denominator=PositiveCount(value=1))
    approximate = ApproximateEvidence(delta=ratio, effective_tolerance=ratio)
    location = SchemaViolationLocation(
        instance=JsonPointer(value="/x"),
        schema=JsonPointer(value="/properties/x"),
    )
    schema = SchemaValidationEvidence(
        violation_count=Count(value=2), retained=(location,), omitted=Count(value=1)
    )
    digest = DigestEvidence(
        algorithm=DigestAlgorithm.SHA512,
        actual=BytesValue(value=b"x" * 64),
    )
    origin = TaintOriginId(value="secret.evidence")
    assert is_assertion_operator_evidence(approximate)
    assert is_assertion_operator_evidence(schema)
    assert is_assertion_operator_evidence(digest)
    assert is_assertion_operator_evidence(SensitiveValue(approximate, origins=(origin,)))
    assert not is_assertion_operator_evidence(object())
    assert _operator_evidence_size(approximate) > 0
    assert _operator_evidence_size(schema) > 0
    assert _operator_evidence_size(digest) > 0
    assert _operator_evidence_size(SensitiveValue(schema, origins=(origin,))) > 0
    with pytest.raises(TypeError, match="unsupported assertion"):
        _operator_evidence_size(object())

    with pytest.raises(TypeError, match="Ratio"):
        ApproximateEvidence(
            delta=cast("Ratio", object()),
            effective_tolerance=ratio,
        )
    with pytest.raises(TypeError, match="JsonPointer"):
        SchemaViolationLocation(
            instance=cast("JsonPointer", object()),
            schema=JsonPointer(value=""),
        )
    with pytest.raises(TypeError, match="Count"):
        SchemaValidationEvidence(
            violation_count=cast("Count", object()),
        )
    with pytest.raises(TypeError, match="typed tuple"):
        SchemaValidationEvidence(
            violation_count=Count(value=0),
            retained=cast("tuple[SchemaViolationLocation, ...]", []),
        )
    with pytest.raises(ValueError, match="unique"):
        SchemaValidationEvidence(
            violation_count=Count(value=2),
            retained=(location, location),
        )
    with pytest.raises(TypeError, match="DigestAlgorithm"):
        DigestEvidence(
            algorithm=cast("DigestAlgorithm", object()),
            actual=BytesValue(value=b"x" * 32),
        )
    with pytest.raises(TypeError, match="seal BytesValue"):
        DigestEvidence(
            algorithm=DigestAlgorithm.SHA256,
            actual=cast(
                "SensitiveValue[BytesValue]",
                SensitiveValue(JsonString(value="x"), origins=(origin,)),
            ),
        )
    with pytest.raises(TypeError, match="public or sealed"):
        DigestEvidence(
            algorithm=DigestAlgorithm.SHA256,
            actual=cast("BytesValue", object()),
        )
    with pytest.raises(ValueError, match="wrong width"):
        DigestEvidence(
            algorithm=DigestAlgorithm.SHA256,
            actual=BytesValue(value=b"short"),
        )

    negative = Ratio(numerator=0, denominator=PositiveCount(value=1))
    msgspec.structs.force_setattr(negative, "numerator", -1)
    with pytest.raises(ValueError, match="cannot be negative"):
        ApproximateEvidence(delta=negative, effective_tolerance=ratio)


def test_numeric_rejects_invalid_inputs_and_negative_tolerances() -> None:
    operator = ContainsOperator(mode=ContainsMode.ONE, expected=(_literal(JsonString(value="x")),))
    with pytest.raises(TypeError, match="numeric"):
        approximate_result(JsonString(value="x"), (JsonInteger(value=1),), cast("object", operator))
    with pytest.raises(TypeError, match="exact assertion number"):
        _fraction(object())

    negative = ApproximateOperator(
        expected=_literal(JsonInteger(value=1)),
        absolute_tolerance=_literal(JsonInteger(value=1)),
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        approximate_result(
            JsonInteger(value=1),
            (JsonInteger(value=1), JsonInteger(value=-1)),
            negative,
        )


def test_remaining_domain_hardening_invariants() -> None:
    contains = ContainsOperator(
        mode=ContainsMode.ONE,
        expected=(_literal(JsonInteger(value=1)),),
    )
    require_assertion_operator_contract(
        ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=rule().selector.output.cardinality,
        ),
        contains,
    )
    with pytest.raises(ValueError, match="validator"):
        msgspec.structs.replace(_schema(), validator=object())

    match = _match(JsonString(value="x"))
    fact_id = FactId(value=UUID(int=5001))
    evidence = ApproximateEvidence(
        delta=Ratio(numerator=0, denominator=PositiveCount(value=1)),
        effective_tolerance=Ratio(numerator=0, denominator=PositiveCount(value=1)),
    )
    with pytest.raises(TypeError, match="unsupported type"):
        MatchFact(
            fact_id=fact_id,
            match=match,
            outcome=MatchOutcome.SATISFIED,
            operator_evidence=cast("object", object()),
        )
    with pytest.raises(ValueError, match="cannot carry"):
        MatchFact(
            fact_id=fact_id,
            match=match,
            outcome=MatchOutcome.ERROR,
            diagnostic=DiagnosticCode(value="assertion.error"),
            operator_evidence=evidence,
        )
    with pytest.raises(ValueError, match="exactly when"):
        AssertionEvidence(retained=(), omitted=Count(value=1))
    truncation = _evidence_exhaustion()
    msgspec.structs.force_setattr(
        truncation,
        "effect",
        BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
    )
    with pytest.raises(ValueError, match="exact evidence"):
        AssertionEvidence(
            retained=(),
            omitted=Count(value=1),
            truncation=truncation,
        )

    exhausted = evaluate_assertion(
        snapshot(),
        application(),
        rule(),
        inputs(),
        control(work=7),
    )
    assert type(exhausted.fact.outcome) is AssertionUnavailable
    assert exhausted.fact.outcome.reason is AssertionUnavailableReason.BUDGET_EXCEEDED
