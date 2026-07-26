"""Close defensive and catalog branches in the application evaluation adapters."""

# These tests intentionally drive many independent defensive ValueError branches;
# matching every private helper message would couple coverage to non-contractual text.
# ruff: noqa: PT011

from __future__ import annotations

import hashlib
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application import digest_validation as digest_module
from comparo.application import evaluation_assertions as assertions_module
from comparo.application import evaluation_binding as binding_module
from comparo.application import evaluation_diffs as diffs_module
from comparo.application import evaluation_items as items_module
from comparo.application import evaluation_selectors as selectors_module
from comparo.application import regex_validation as regex_module
from comparo.application import schema_validation as schema_module
from comparo.core.assertions.inputs import PreparedExpression as PreparedAssertionExpression
from comparo.core.assertions.inputs import PreparedOperandReady
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.assertions.extended_operators import CompiledSchema
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.regex import RegexMode
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.planning import plan_run
from comparo.core.policy.trusted import trusted_local_policy
from tests.application.test_evaluation_binding import _bind
from tests.application.test_evaluation_binding import _context
from tests.application.test_evaluation_binding import _limits
from tests.application.test_evaluation_binding import _prepared
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def _literal(value: object) -> LiteralExpr:
    return LiteralExpr(value=cast("Any", value), provenance=_provenance())


def _text(value: str) -> LiteralExpr:
    return _literal(JsonString(value=value))


def _integer(value: int) -> LiteralExpr:
    return _literal(JsonInteger(value=value))


def _boolean(value: bool) -> LiteralExpr:
    return _literal(JsonBoolean(value=value))


def _array(*items: ValueExpression) -> ArrayExpr:
    return ArrayExpr(items=items, provenance=_provenance())


def _object(fields: dict[str, ValueExpression]) -> ObjectExpr:
    return ObjectExpr(
        members=tuple(
            ObjectExpressionMember(name=name, expression=value) for name, value in fields.items()
        ),
        provenance=_provenance(),
    )


def _operation(name: str, **fields: ValueExpression) -> ObjectExpr:
    return _object({"name": _text(name), **fields})


def _fingerprint(character: str = "7") -> Fingerprint:
    from comparo.core.domain.primitives import Sha256Digest

    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value=character * 64))


def _contract(
    kind: ValueKind = ValueKind.UNKNOWN,
    cardinality: SelectorCardinality = SelectorCardinality.EXACTLY_ONE,
) -> ValueContract:
    return ValueContract(kinds=(kind,), cardinality=cardinality)


def _match(value: object) -> SelectionMatch:
    execution_id = EnvironmentExecutionId(value=UUID(int=77_001))
    return SelectionMatch(
        value=cast("Any", value),
        location=BodyLocation(
            environment_execution_id=execution_id,
            source=SelectorSource.RESPONSE_BODY,
            body_id=BodyId(value=UUID(int=77_002)),
            body_view=BodyViewKind.ENTITY_BYTES,
        ),
    )


@pytest.mark.parametrize(
    "expression",
    [
        _operation("less", expected=_integer(2)),
        _operation(
            "between",
            lower=_integer(1),
            upper=_integer(2),
            includeLower=_boolean(False),
            includeUpper=_boolean(True),
        ),
        _operation("approximatelyEquals", expected=_integer(2), absolute=_integer(1)),
        _operation("containsAll", expected=_array(_text("a"))),
        _operation("containsAny", expected=_array(_text("a"))),
        _operation("hasKey", key=_text("a")),
        _operation("hasKeys", keys=_array(_text("a"))),
        _operation("hasOnlyKeys", keys=_array(_text("a"))),
        _operation("length", expected=_integer(1), relation=_text("at-least")),
        _operation("decodesAs", view=_text("json")),
        _operation("mediaTypeIs", expected=_text("application/json"), relation=_text("exact")),
        _operation("mediaTypeMatches", expected=_text("application/problem+json")),
        _operation("containsToken", token=_text("gzip")),
        _operation("cardinality", minimum=_integer(0), maximum=_integer(2)),
        _operation("matches", pattern=_text("ok"), mode=_text("full")),
    ],
)
def test_assertion_catalog_variants_compile(expression: ObjectExpr) -> None:
    identity, operator = assertions_module.compile_assertion_operation(expression)

    assert identity.namespace == "comparo.core"
    assert operator is not None


@pytest.mark.parametrize(
    "expression",
    [
        None,
        _integer(1),
        _operation("unsupported"),
        _operation("in", choices=_text("not-an-array")),
        _operation("in", choices=_array()),
        _operation("hasKeys", keys=_array(_integer(1))),
        _operation("isType", expected=_integer(1)),
        _operation("length", expected=_text("one")),
        _operation("between", lower=_integer(1), upper=_integer(2), includeLower=_text("yes")),
        _operation("cardinality", minimum=_text("one")),
        _operation("mediaTypeIs", expected=_text("text/plain;charset=utf-8")),
        _operation("mediaTypeIs", expected=_text("plain")),
    ],
)
def test_assertion_catalog_rejects_invalid_typed_arguments(
    expression: ValueExpression | None,
) -> None:
    with pytest.raises(assertions_module.InvalidAssertionOperationError):
        assertions_module.compile_assertion_operation(expression)


def test_item_selector_and_predicate_defensive_paths() -> None:
    contract = _contract(ValueKind.OBJECT)
    with pytest.raises(TypeError):
        items_module.compile_item_selector(_text(""), cast("ValueContract", object()), label="x")
    with pytest.raises(TypeError):
        items_module.compile_item_selector(_text(""), contract, label="")
    with pytest.raises(ValueError):
        items_module.compile_item_selector(_integer(1), contract, label="x")
    with pytest.raises(ValueError):
        items_module.compile_item_selector(_text("not/a/pointer"), contract, label="x")

    selector = items_module.compile_item_selector(_text(""), contract, label="x")
    assert type(selector.path) is NoPath
    predicate_selector = items_module.compile_item_selector(
        _text(""), _contract(), label="predicate"
    )
    with pytest.raises(TypeError):
        items_module.compile_item_predicate(
            _operation("equals", expected=_integer(1)), cast("Any", object())
        )
    with pytest.raises(ValueError):
        items_module.compile_item_predicate(_text("equals"), predicate_selector)
    with pytest.raises(ValueError):
        items_module.compile_item_predicate(_operation("equals"), predicate_selector)
    assert (
        items_module.compile_item_predicate(
            _operation("notEquals", expected=_integer(1)), predicate_selector
        ).identity.kind.value
        == "not-equals"
    )
    with pytest.raises(ValueError):
        items_module.compile_item_predicate(_operation("isType"), predicate_selector)
    with pytest.raises(ValueError):
        items_module.compile_item_predicate(
            _operation("isType", expected=_integer(1)), predicate_selector
        )
    assert (
        items_module.compile_item_predicate(
            _operation("isType", expected=_text("object")), predicate_selector
        ).identity.kind.value
        == "is-type"
    )
    with pytest.raises(ValueError):
        items_module.compile_item_predicate(_operation("unknown"), predicate_selector)


def test_selector_shorthand_structured_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    fingerprint = _fingerprint()
    compile_selector = selectors_module.compile_selector
    assert (
        compile_selector(
            _text("request.headers:x-id"),
            fingerprint=fingerprint,
            default_body_view=BodyViewKind.JSON,
        ).source
        is SelectorSource.REQUEST_HEADERS
    )
    assert (
        compile_selector(
            _text("response.status"),
            fingerprint=fingerprint,
            default_body_view=BodyViewKind.JSON,
        ).source
        is SelectorSource.RESPONSE_STATUS
    )
    assert (
        compile_selector(
            _text("execution.id"),
            fingerprint=fingerprint,
            default_body_view=BodyViewKind.JSON,
        ).source
        is SelectorSource.EXECUTION
    )
    assert (
        compile_selector(
            _object({"source": _text("response.body")}),
            fingerprint=fingerprint,
            default_body_view=BodyViewKind.TEXT,
        ).body_view
        is BodyViewKind.TEXT
    )

    invalid = (
        _object({"extra": _text("x")}),
        _object({"source": _integer(1)}),
        _object({"source": _text("invalid")}),
        _object({"source": _text("response.body"), "field": _text("x"), "path": _text("")}),
        _object({"source": _text("response.status"), "view": _text("json")}),
        _object({"source": _text("response.body"), "view": _integer(1)}),
        _object({"source": _text("response.body"), "view": _text("invalid")}),
        _object({"source": _text("response.body"), "occurrence": _text("invalid")}),
        _object({"source": _text("response.headers"), "field": _integer(1)}),
        _object({"source": _text("response.headers"), "field": _object({"extra": _text("x")})}),
        _object({"source": _text("response.headers"), "field": _object({"name": _integer(1)})}),
        _object(
            {
                "source": _text("response.headers"),
                "field": _object({"name": _text("x"), "occurrence": _text("zero")}),
            }
        ),
        _object(
            {
                "source": _text("response.headers"),
                "field": _object({"name": _text("x"), "projection": _text("invalid")}),
            }
        ),
        _object({"source": _text("response.body"), "path": _integer(1)}),
        _object({"source": _text("response.body"), "path": _text("$[")}),
    )
    for expression in invalid:
        with pytest.raises(selectors_module.InvalidSelectorError):
            compile_selector(
                expression,
                fingerprint=fingerprint,
                default_body_view=BodyViewKind.JSON,
            )

    for occurrence in ("terminal", "allAttempts", "allExchanges"):
        assert selectors_module._occurrence(_text(occurrence)) is not None
    assert selectors_module._path(SelectorSource.EXECUTION, _text("payload.value")) is not None

    with pytest.raises(TypeError):
        compile_selector(
            _text("status"),
            fingerprint=cast("Fingerprint", object()),
            default_body_view=BodyViewKind.JSON,
        )

    original = CompiledSelector

    def reject(**_kwargs: object) -> object:
        raise ValueError

    monkeypatch.setattr(selectors_module, "CompiledSelector", reject)
    with pytest.raises(selectors_module.InvalidSelectorError):
        compile_selector(
            _text("status"), fingerprint=fingerprint, default_body_view=BodyViewKind.JSON
        )
    monkeypatch.setattr(selectors_module, "CompiledSelector", original)

    class FakeReady:
        path = NoPath()

    monkeypatch.setattr(selectors_module, "PathCompileReady", FakeReady)
    monkeypatch.setattr(selectors_module, "try_compile_json_pointer", lambda _value: FakeReady())
    with pytest.raises(selectors_module.InvalidSelectorError):
        selectors_module._path(SelectorSource.RESPONSE_BODY, _text("/x"))
    assert selectors_module._bounded_alias("x" * 65) is None


@pytest.mark.parametrize(
    "expression",
    [
        _text("project"),
        _text("exclude"),
        _operation("length", unit=_text("items")),
        _operation("valueType"),
        _operation("digest", algorithm=_text("sha256")),
        _operation("relativeSelect", path=_text("")),
        _operation("streamProject", source=_text("sse-data"), path=_text("")),
    ],
)
def test_diff_transform_catalog_variants(expression: ValueExpression) -> None:
    if expression == _text("project") or expression == _text("exclude"):
        assert type(expression) is LiteralExpr
        assert type(expression.value) is JsonString
        expression = _operation(expression.value.value, pointers=_array(_text("/x")))
    result = diffs_module.compile_transforms(_array(expression), input_contract=_contract())
    assert len(result) == 1


def test_diff_compiler_error_and_catalog_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = _contract()
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_transforms(_text("bad"), input_contract=contract)
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_transforms(_array(_integer(1)), input_contract=contract)
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_transforms(_array(_text("unsupported")), input_contract=contract)
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_reducer(_integer(1), input_contract=contract, transforms=())
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_pairing(_integer(1), baseline=contract, candidate=contract)
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_pairing(_text("unsupported"), baseline=contract, candidate=contract)
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_comparator(_integer(1))

    for name in ("type", "jsonShape", "subset", "superset", "ordered", "set", "multiset"):
        assert diffs_module.compile_comparator(_text(name)) is not None
    assert (
        diffs_module.compile_comparator(
            _operation("textPredicate", relation=_text("baseline-contains-candidate"))
        )
        is not None
    )
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_comparator(_text("unsupported"))

    assert (
        diffs_module.compile_reducer(
            _operation("concatenate", separator=_text(",")), input_contract=contract, transforms=()
        )
        is not None
    )
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_reducer(_text("concatenate"), input_contract=contract, transforms=())
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_reducer(_text("unsupported"), input_contract=contract, transforms=())

    assert diffs_module._operation(_object({"other": _text("x")})) is None
    assert diffs_module._kebab("jsonShape") == "json-shape"
    with pytest.raises(ValueError):
        diffs_module._require_optional_fields({}, {"name"}, set())
    with pytest.raises(ValueError):
        diffs_module._texts(_array())
    with pytest.raises(ValueError):
        diffs_module._texts(_array(_integer(1)))
    with pytest.raises(ValueError):
        diffs_module._required_text({"x": _integer(1)}, "x")
    assert (
        diffs_module._invalid(diffs_module.DiffSyntaxFamily.TRANSFORM, "x" * 65).error.alias is None
    )

    def reject_contract(*_args: object, **_kwargs: object) -> object:
        raise ValueError

    monkeypatch.setattr(diffs_module, "derive_side_contract", reject_contract)
    with pytest.raises(diffs_module.InvalidDiffOperationError):
        diffs_module.compile_reducer(_text("count"), input_contract=contract, transforms=())


def test_digest_validation_success_failure_sensitivity_and_limits() -> None:
    with pytest.raises(ValueError):
        digest_module.DigestValidationLimits(maximum_input_bytes=cast("int", True))
    limits = digest_module.DigestValidationLimits(maximum_input_bytes=3)
    execution_id = EnvironmentExecutionId(value=UUID(int=77_001))
    expected = hashlib.sha256(b"abc").hexdigest()
    operator = DigestOperator(algorithm=DigestAlgorithm.SHA256, expected=_text(expected))
    invalid_input = digest_module.prepare_digest_predicate(
        operator,
        _match(JsonString(value="abc")),
        PreparedOperandReady(value=JsonString(value=expected)),
        execution_id,
        _fingerprint(),
        limits,
    )
    oversized = digest_module.prepare_digest_predicate(
        operator,
        _match(BytesValue(value=b"abcd")),
        PreparedOperandReady(value=JsonString(value=expected)),
        execution_id,
        _fingerprint(),
        limits,
    )
    invalid_operand = digest_module.prepare_digest_predicate(
        operator,
        _match(BytesValue(value=b"abc")),
        PreparedOperandReady(value=JsonString(value="bad")),
        execution_id,
        _fingerprint(),
        limits,
    )
    assert (
        invalid_input.outcome
        is oversized.outcome
        is invalid_operand.outcome
        is PreparedPredicateOutcome.ERROR
    )

    secret = TaintOriginId(value="test/digest")
    sha512 = hashlib.sha512(b"abc").hexdigest()
    sensitive = digest_module.prepare_digest_predicate(
        DigestOperator(algorithm=DigestAlgorithm.SHA512, expected=_text(sha512)),
        _match(SensitiveValue(BytesValue(value=b"abc"), origins=(secret,))),
        PreparedOperandReady(value=SensitiveValue(JsonString(value=sha512), origins=(secret,))),
        execution_id,
        _fingerprint(),
        limits,
    )
    satisfied = digest_module.prepare_digest_predicate(
        DigestOperator(algorithm=DigestAlgorithm.SHA512, expected=_text(sha512)),
        _match(BytesValue(value=b"abc")),
        PreparedOperandReady(value=JsonString(value=sha512)),
        execution_id,
        _fingerprint(),
        limits,
    )
    violated = digest_module.prepare_digest_predicate(
        operator,
        _match(BytesValue(value=b"abc")),
        PreparedOperandReady(value=JsonString(value="0" * 64)),
        execution_id,
        _fingerprint(),
        limits,
    )
    assert satisfied.outcome is PreparedPredicateOutcome.SATISFIED
    assert sensitive.outcome is PreparedPredicateOutcome.SATISFIED
    assert is_sensitive_value(sensitive.evidence)
    assert sensitive.evidence.origins == (secret,)
    assert violated.outcome is PreparedPredicateOutcome.VIOLATED


def test_regex_validation_contracts_catalog_and_matching(monkeypatch: pytest.MonkeyPatch) -> None:
    for values in ((0, 1), (1, cast("int", True))):
        with pytest.raises(ValueError):
            regex_module.RegexValidationLimits(
                maximum_pattern_bytes=values[0], maximum_input_bytes=values[1]
            )
    limits = regex_module.RegexValidationLimits(maximum_pattern_bytes=10, maximum_input_bytes=3)
    with pytest.raises(TypeError):
        regex_module.PreparedRegex(
            operator=cast("Any", object()),
            program=cast("Any", object()),
        )
    with pytest.raises(TypeError):
        regex_module.PreparedRegexCatalog(cast("Any", []), limits)
    with pytest.raises(TypeError):
        regex_module.PreparedRegexCatalog((), cast("Any", object()))

    prepared = regex_module.compile_regex("a", mode=RegexMode.SEARCH, negate=False, limits=limits)
    with pytest.raises(ValueError):
        regex_module.PreparedRegexCatalog((prepared, prepared), limits)
    catalog = regex_module.PreparedRegexCatalog((prepared,), limits)
    execution_id = EnvironmentExecutionId(value=UUID(int=77_001))
    assert catalog.entries == (prepared,)
    assert (
        regex_module.PreparedRegexCatalog((), limits)
        .predicate(prepared.operator, _match(JsonString(value="a")), execution_id, _fingerprint())
        .outcome
        is PreparedPredicateOutcome.ERROR
    )
    assert (
        catalog.predicate(
            prepared.operator, _match(JsonInteger(value=1)), execution_id, _fingerprint()
        ).outcome
        is PreparedPredicateOutcome.ERROR
    )
    assert (
        catalog.predicate(
            prepared.operator, _match(JsonString(value="abcd")), execution_id, _fingerprint()
        ).outcome
        is PreparedPredicateOutcome.ERROR
    )
    assert (
        catalog.predicate(
            prepared.operator, _match(JsonString(value="b")), execution_id, _fingerprint()
        ).outcome
        is PreparedPredicateOutcome.VIOLATED
    )

    full = regex_module.compile_regex("a", mode=RegexMode.FULL, negate=False, limits=limits)
    assert (
        regex_module.PreparedRegexCatalog((full,), limits)
        .predicate(full.operator, _match(JsonString(value="a")), execution_id, _fingerprint())
        .outcome
        is PreparedPredicateOutcome.SATISFIED
    )

    with pytest.raises(TypeError):
        regex_module.compile_regex(
            cast("str", object()), mode=RegexMode.SEARCH, negate=False, limits=limits
        )
    with pytest.raises(ValueError):
        regex_module.compile_regex("x" * 11, mode=RegexMode.SEARCH, negate=False, limits=limits)
    with pytest.raises(ValueError):
        regex_module.compile_regex("(", mode=RegexMode.SEARCH, negate=False, limits=limits)

    altered = msgspec.structs.replace(prepared.operator, compiled_fingerprint=_fingerprint("8"))
    with pytest.raises(ValueError):
        regex_module.prepare_compiled_regex(altered, limits)


def test_schema_validation_limits_carriers_catalog_and_conversion() -> None:
    values = dict.fromkeys(schema_module.SchemaValidationLimits.__struct_fields__, 1)
    for field in values:
        invalid = dict(values)
        invalid[field] = 0
        with pytest.raises(ValueError):
            schema_module.SchemaValidationLimits(**invalid)
    with pytest.raises(ValueError):
        schema_module.SchemaValidationLimits(
            maximum_schema_depth=1,
            maximum_schema_nodes=1,
            maximum_instance_depth=1,
            maximum_instance_nodes=1,
            maximum_violations=1,
            retained_violations=2,
        )

    compiled = CompiledSchema(
        fingerprint=_fingerprint(),
        remote_uri=NamedCapabilityTarget(
            kind=CapabilityTargetKind.SCHEMA_SOURCE,
            identifier="https://example.test/schema",
        ),
    )
    prepared = schema_module.PreparedSchema(compiled=compiled, validator=object())
    with pytest.raises(TypeError):
        schema_module.PreparedSchema(compiled=cast("Any", object()), validator=object())
    with pytest.raises(TypeError):
        schema_module.PreparedSchema(compiled=compiled, validator=None)
    with pytest.raises(TypeError):
        schema_module.PreparedSchemaCatalog(
            cast("Any", []), schema_module.DEFAULT_SCHEMA_VALIDATION_LIMITS_V1
        )
    with pytest.raises(TypeError):
        schema_module.PreparedSchemaCatalog((), cast("Any", object()))
    with pytest.raises(ValueError):
        schema_module.PreparedSchemaCatalog(
            (prepared, prepared), schema_module.DEFAULT_SCHEMA_VALIDATION_LIMITS_V1
        )

    document = schema_module.RemoteSchemaDocument(uri="https://example.test/schema", payload=b"{}")
    with pytest.raises(ValueError):
        schema_module.RemoteSchemaDocument(uri="https://example.test/schema", payload=b"")
    with pytest.raises(TypeError):
        schema_module.RemoteSchemaDocuments(entries=cast("Any", [document]))
    with pytest.raises(ValueError):
        schema_module.RemoteSchemaDocuments(entries=(document, document))
    documents = schema_module.RemoteSchemaDocuments(entries=(document,))
    assert documents.get(document.uri) == b"{}"
    assert documents.get("https://missing.test/schema") is None

    values_to_convert = (
        JsonNull(),
        JsonBoolean(value=True),
        JsonInteger(value=1),
        JsonDecimal(value=Decimal("1.5")),
        JsonString(value="x"),
        JsonArray(items=(JsonInteger(value=1),)),
        JsonObject(members=(JsonObjectMember(name="x", value=JsonInteger(value=1)),)),
        None,
        True,
        1,
        Decimal("1.5"),
        "x",
        [1],
        {"x": 1},
    )
    for value in values_to_convert:
        schema_module._to_builtin(value, maximum_depth=10, maximum_nodes=20)
    for invalid_value in ({1: "bad"}, object(), [1, [2]]):
        with pytest.raises(ValueError):
            schema_module._to_builtin(invalid_value, maximum_depth=1, maximum_nodes=2)


def test_schema_preparation_predicates_and_reference_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = schema_module.SchemaValidationLimits(
        maximum_schema_depth=10,
        maximum_schema_nodes=100,
        maximum_instance_depth=3,
        maximum_instance_nodes=10,
        maximum_violations=1,
        retained_violations=1,
    )
    request_prepared = _prepared(response_schema="{type: string}")
    owner = request_prepared.effective.subjects[0].definition
    resource = request_prepared.project.find(owner.identity.kind, owner.identity.resource_id)
    assert resource is not None
    assert type(resource.spec) is CanonicalObject
    binding_value = cast(
        "Any",
        next(member.value for member in resource.spec.members if member.name == "responseSchema"),
    )
    prepared = schema_module.prepare_local_schema(
        request_prepared.project, owner, binding_value, limits
    )
    operator = schema_module.schema_operator(prepared)
    execution_id = EnvironmentExecutionId(value=UUID(int=77_001))
    catalog = schema_module.PreparedSchemaCatalog((prepared,), limits)
    assert catalog.entries == (prepared,)
    assert (
        catalog.predicate(
            operator, _match(JsonString(value="ok")), execution_id, _fingerprint()
        ).outcome
        is PreparedPredicateOutcome.SATISFIED
    )
    assert (
        catalog.predicate(
            operator, _match(JsonInteger(value=1)), execution_id, _fingerprint()
        ).outcome
        is PreparedPredicateOutcome.VIOLATED
    )
    assert (
        schema_module.PreparedSchemaCatalog((), limits)
        .predicate(operator, _match(JsonString(value="ok")), execution_id, _fingerprint())
        .outcome
        is PreparedPredicateOutcome.ERROR
    )

    tiny = msgspec.structs.replace(limits, maximum_instance_nodes=1, maximum_instance_depth=1)
    assert (
        schema_module.PreparedSchemaCatalog((prepared,), tiny)
        .predicate(
            operator,
            _match(JsonArray(items=(JsonString(value="a"), JsonString(value="b")))),
            execution_id,
            _fingerprint(),
        )
        .outcome
        is PreparedPredicateOutcome.ERROR
    )

    sensitive = schema_module.PreparedSchemaCatalog((prepared,), limits).predicate(
        operator,
        _match(
            SensitiveValue(JsonString(value="ok"), origins=(TaintOriginId(value="schema/test"),))
        ),
        execution_id,
        _fingerprint(),
    )
    assert isinstance(sensitive.evidence, SensitiveValue)

    linked_project = compile_project(
        document(
            "Project",
            "linked",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: staging}]}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document(
            "Request",
            "check",
            "{request: {method: GET, endpoint: /check}, responseSchema: {$use: Schema/response}}",
        ),
        document("Schema", "response", "{type: string}"),
    )
    linked_plan = plan_run(linked_project, invocation(), authority())
    assert linked_plan.prepared is not None
    linked_owner = linked_plan.prepared.effective.subjects[0].definition
    linked_request = linked_project.find(
        linked_owner.identity.kind,
        linked_owner.identity.resource_id,
    )
    assert linked_request is not None
    assert type(linked_request.spec) is CanonicalObject
    linked_binding = next(
        member.value for member in linked_request.spec.members if member.name == "responseSchema"
    )
    assert (
        schema_module.prepare_local_schema(
            linked_project,
            linked_owner,
            linked_binding,
            limits,
        ).compiled.resource
        is not None
    )
    assert type(linked_binding) is LinkedResource
    missing_link = msgspec.structs.replace(
        linked_binding,
        target=msgspec.structs.replace(linked_binding.target, fingerprint=_fingerprint("9")),
    )
    with pytest.raises(ValueError, match="absent"):
        schema_module.prepare_local_schema(
            linked_project,
            linked_owner,
            missing_link,
            limits,
        )

    with pytest.raises(TypeError):
        catalog.predicate(
            cast("Any", object()), _match(JsonString(value="x")), execution_id, _fingerprint()
        )
    with pytest.raises(TypeError):
        catalog.predicate(
            operator, _match(JsonString(value="x")), cast("Any", object()), _fingerprint()
        )
    with pytest.raises(TypeError):
        catalog.predicate(
            operator, _match(JsonString(value="x")), execution_id, cast("Any", object())
        )

    with pytest.raises(TypeError):
        schema_module.prepare_local_schema(cast("Any", object()), owner, binding_value, limits)
    with pytest.raises(TypeError):
        schema_module.prepare_local_schema(
            request_prepared.project, owner, binding_value, cast("Any", object())
        )
    with pytest.raises(ValueError):
        schema_module.prepare_local_schema(
            request_prepared.project,
            owner,
            RemoteSchema(uri="https://example.test/schema"),
            limits,
        )
    with pytest.raises(TypeError):
        schema_module.prepare_remote_schema(cast("Any", object()), b"{}", limits)
    with pytest.raises(TypeError):
        schema_module.prepare_remote_schema(
            RemoteSchema(uri="https://example.test/schema"), b"{}", cast("Any", object())
        )
    for payload in (b"\xff", b"{"):
        with pytest.raises(ValueError):
            schema_module.prepare_remote_schema(
                RemoteSchema(uri="https://example.test/schema"), payload, limits
            )
    with pytest.raises(ValueError):
        schema_module.prepare_remote_schema(
            RemoteSchema(uri="https://example.test/schema"),
            b'{"$ref":"file:///tmp/schema.json"}',
            limits,
        )
    remote = schema_module.prepare_remote_schema(
        RemoteSchema(uri="https://example.test/schema"), b'{"type":"string"}', limits
    )
    assert remote.compiled.remote_uri is not None
    with pytest.raises(TypeError):
        schema_module.schema_operator(cast("Any", object()))
    with pytest.raises(ValueError):
        schema_module._validator({"type": "unsupported"})

    class NoErrors:
        def iter_errors(self, _instance: object) -> tuple[object, ...]:
            return ()

    assert schema_module._violations(object(), {}, limits) is None
    assert schema_module._violations(NoErrors(), {}, limits) == (0, ())

    class TooMany:
        def iter_errors(self, _instance: object) -> tuple[object, ...]:
            error = SimpleNamespace(absolute_path=("a/b",), absolute_schema_path=("x~y",))
            return error, error

    assert schema_module._violations(TooMany(), {}, limits) is None
    retained_limit = msgspec.structs.replace(
        limits,
        maximum_violations=2,
        retained_violations=1,
    )
    retained = schema_module._violations(TooMany(), {}, retained_limit)
    assert retained is not None
    assert retained[0] == 2
    assert len(retained[1]) == 1
    assert schema_module._pointer(("a/b", "x~y", 1)) == "/a~1b/x~0y/1"
    schema_module._reject_remote_references([{"$ref": "#/$defs/x"}, {"$ref": 1}])
    for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
        with pytest.raises(ValueError):
            schema_module._reject_remote_references({keyword: "file:///tmp/schema.json"})

    monkeypatch.setattr(schema_module, "_violations", lambda *_args: None)
    assert (
        catalog.predicate(
            operator, _match(JsonString(value="ok")), execution_id, _fingerprint()
        ).outcome
        is PreparedPredicateOutcome.ERROR
    )


def test_evaluation_binding_and_preflight_boundary_contracts() -> None:
    prepared = _prepared()
    context = _context(prepared)
    limits = _limits()
    valid = cast(
        "binding_module.ConfigurationEvaluationPreparation",
        _bind(prepared).preparation,
    )
    invalid = binding_module.preflight_configuration_evaluation(_prepared(assertion="[]"))
    assert not invalid.supported
    diagnostic = invalid.diagnostics[0]
    rejected = binding_module.RejectedEvaluationCapability(
        family=binding_module.EvaluationCapabilityFamily.SELECTOR,
        alias=None,
        resource=prepared.project.project.definition.identity,
    )
    gate = cast(
        "binding_module.ConfigurationEvaluationPreparation",
        _bind(_prepared(gate="{}")).preparation,
    ).gate_profile
    assert gate is not None

    with pytest.raises(TypeError, match="diagnostics"):
        binding_module.EvaluationPreflightResult(
            diagnostics=cast("Any", [diagnostic]),
            rejected=(),
            assertion_rules=0,
            diff_rules=0,
            gate=None,
        )
    with pytest.raises(TypeError, match="rejections"):
        binding_module.EvaluationPreflightResult(
            diagnostics=(),
            rejected=cast("Any", [rejected]),
            assertion_rules=0,
            diff_rules=0,
            gate=None,
        )
    for count in (-1, cast("int", True)):
        with pytest.raises(ValueError, match="non-negative"):
            binding_module.EvaluationPreflightResult(
                diagnostics=(),
                rejected=(),
                assertion_rules=count,
                diff_rules=0,
                gate=None,
            )
    with pytest.raises(TypeError, match="gate"):
        binding_module.EvaluationPreflightResult(
            diagnostics=(),
            rejected=(),
            assertion_rules=0,
            diff_rules=0,
            gate=cast("Any", object()),
        )
    with pytest.raises(ValueError, match="cannot retain"):
        binding_module.EvaluationPreflightResult(
            diagnostics=(diagnostic,),
            rejected=(),
            assertion_rules=1,
            diff_rules=0,
            gate=gate,
        )

    constructor = {
        "prepared": prepared,
        "context": context,
        "limits": limits,
        "subjects": valid._subjects,
        "gate": None,
        "schemas": valid._schemas,
        "regexes": valid._regexes,
        "digest_limits": valid._digest_limits,
    }
    for field, message in (
        ("schemas", "schemas"),
        ("regexes", "regexes"),
        ("digest_limits", "digest limits"),
    ):
        invalid_constructor = dict(constructor)
        invalid_constructor[field] = object()
        with pytest.raises(TypeError, match=message):
            cast("Any", binding_module.ConfigurationEvaluationPreparation)(**invalid_constructor)

    for field, value, message in (
        ("schema_limits", object(), "SchemaValidationLimits"),
        ("regex_limits", object(), "RegexValidationLimits"),
        ("digest_limits", object(), "DigestValidationLimits"),
        ("policy", object(), "policy"),
        ("remote_documents", object(), "remote schemas"),
    ):
        kwargs: dict[str, object] = {field: value}
        with pytest.raises(TypeError, match=message):
            cast("Any", binding_module.bind_configuration_evaluation)(
                prepared,
                context,
                limits,
                **kwargs,
            )

    for args, kwargs, message in (
        ((object(),), {}, "PreparedRun"),
        ((prepared, object()), {}, "SchemaValidationLimits"),
        ((prepared,), {"regex_limits": object()}, "RegexValidationLimits"),
        ((prepared,), {"policy": object()}, "policy"),
    ):
        with pytest.raises(TypeError, match=message):
            cast("Any", binding_module.preflight_configuration_evaluation)(*args, **kwargs)


def test_configuration_compiler_regex_dedup_failure_and_schema_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_prepared = _prepared(
        assertion=(
            "[{id: one, selector: {source: response.body, view: text}, "
            "operator: {name: matches, pattern: ok}}, "
            "{id: two, selector: {source: response.body, view: text}, "
            "operator: {name: matches, pattern: ok}}]"
        )
    )
    deduplicated = _bind(regex_prepared)
    assert deduplicated.preparation is not None
    assert len(deduplicated.preparation._regexes.entries) == 1

    def reject_regex(*_args: object, **_kwargs: object) -> object:
        raise ValueError

    monkeypatch.setattr(binding_module, "prepare_compiled_regex", reject_regex)
    rejected = binding_module.preflight_configuration_evaluation(regex_prepared)
    assert not rejected.supported
    assert rejected.rejected[0].alias == "matches"
    monkeypatch.undo()

    response = _prepared(response_schema="{$use: 'https://schemas.example.test/response.json'}")
    without_document = binding_module.bind_configuration_evaluation(
        response,
        _context(response),
        _limits(),
        policy=trusted_local_policy(),
    )
    assert without_document.preparation is None
    assert tuple(item.code.value for item in without_document.diagnostics) == (
        "evaluation.schema.invalid",
    )

    subject = response.effective.subjects[0]
    environment = response.effective.environments[0]
    fake_environment_subject = SimpleNamespace(definition=environment)
    problems = binding_module._Problems()
    entries: list[schema_module.PreparedSchema] = []
    assert (
        binding_module._compile_response_schema(
            response.project,
            cast("Any", fake_environment_subject),
            entries,
            schema_module.DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
            None,
            None,
            False,
            problems,
        )
        is None
    )

    missing_definition = msgspec.structs.replace(
        subject.definition,
        fingerprint=_fingerprint("6"),
    )
    fake_missing_subject = SimpleNamespace(definition=missing_definition)
    assert (
        binding_module._compile_response_schema(
            response.project,
            cast("Any", fake_missing_subject),
            entries,
            schema_module.DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
            None,
            None,
            False,
            problems,
        )
        is None
    )
    assert any(
        item.code.value == "evaluation.profile.inconsistent" for item in problems.diagnostics
    )


def test_empty_profiles_diff_sides_gate_and_metadata_defensive_paths() -> None:
    assertion_prepared = _prepared(
        assertion="[{selector: status, operator: {name: equals, expected: 200}}]"
    )
    assertion_handle = cast(
        "DefinitionRef",
        assertion_prepared.effective.subjects[0].profiles.assertions[0],
    )
    assertion_profile = assertion_prepared.project.find(
        assertion_handle.identity.kind,
        assertion_handle.identity.resource_id,
    )
    assert assertion_profile is not None
    empty_profile = msgspec.structs.replace(
        assertion_profile,
        spec=CanonicalObject(members=()),
    )
    problems = binding_module._Problems()
    assert binding_module._compile_assertion_profile(empty_profile, problems) == ()
    include_only_assertion = msgspec.structs.replace(
        assertion_profile,
        spec=CanonicalObject(
            members=(CanonicalMember(name="include", value=CanonicalArray(items=())),)
        ),
    )
    include_problems = binding_module._Problems()
    assert (
        binding_module._compile_assertion_profile(
            include_only_assertion,
            include_problems,
        )
        == ()
    )
    assert include_problems.diagnostics == []

    diff_prepared = _prepared(diff="[{selector: status, comparator: exact}]")
    diff_handle = cast(
        "DefinitionRef",
        diff_prepared.effective.subjects[0].profiles.diffs[0],
    )
    diff_profile = diff_prepared.project.find(
        diff_handle.identity.kind,
        diff_handle.identity.resource_id,
    )
    assert diff_profile is not None
    assert (
        binding_module._compile_diff_profile(
            msgspec.structs.replace(diff_profile, spec=CanonicalObject(members=())),
            problems,
        )
        == ()
    )
    include_only_diff = msgspec.structs.replace(
        diff_profile,
        spec=CanonicalObject(
            members=(CanonicalMember(name="include", value=CanonicalArray(items=())),)
        ),
    )
    include_problems = binding_module._Problems()
    assert binding_module._compile_diff_profile(include_only_diff, include_problems) == ()
    assert include_problems.diagnostics == []

    invalid_fields = _operation(
        "ignored",
        selector=_text("status"),
        comparator=_text("exact"),
        unexpected=_text("x"),
    )
    assert (
        binding_module._compile_diff_rule(
            diff_profile,
            invalid_fields,
            0,
            problems,
        )
        is None
    )
    shared = {
        "selector": _text("status"),
        "comparator": _text("exact"),
    }
    assert (
        binding_module._compile_diff_side(
            {**shared, "baseline": _text("bad")},
            "baseline",
            diff_profile,
            0,
            problems,
        )
        is None
    )
    assert (
        binding_module._compile_diff_side(
            {**shared, "candidate": _object({"unexpected": _text("x")})},
            "candidate",
            diff_profile,
            0,
            problems,
        )
        is None
    )

    assert binding_module._gate_enum_array(None, GateSeverity, (GateSeverity.BLOCKING,)) == (
        GateSeverity.BLOCKING,
    )
    for value in (JsonString(value="bad"), JsonArray(items=())):
        with pytest.raises(ValueError):
            binding_module._gate_enum_array(value, GateSeverity, (GateSeverity.BLOCKING,))
    with pytest.raises(ValueError):
        binding_module._gate_enum_array(
            JsonArray(items=(JsonString(value="blocking"), JsonString(value="blocking"))),
            GateSeverity,
            (GateSeverity.BLOCKING,),
        )
    with pytest.raises(ValueError):
        binding_module._gate_boolean(JsonString(value="true"), default=True)
    with pytest.raises(ValueError):
        binding_module._gate_thresholds(JsonArray(items=()), (GateSeverity.BLOCKING,))
    threshold = JsonObject(
        members=(
            JsonObjectMember(name="severity", value=JsonString(value="warning")),
            *(
                JsonObjectMember(name=name, value=JsonInteger(value=0))
                for name in (
                    "violatedRules",
                    "driftedRules",
                    "unavailableRules",
                    "incomparableRules",
                    "incompleteRules",
                    "errorRules",
                )
            ),
        )
    )
    with pytest.raises(ValueError):
        binding_module._gate_thresholds(
            JsonArray(items=(threshold,)),
            (GateSeverity.BLOCKING,),
        )
    with pytest.raises(ValueError):
        binding_module._gate_threshold(JsonString(value="bad"))
    with pytest.raises(ValueError):
        binding_module._gate_threshold(JsonObject(members=()))
    with pytest.raises(ValueError):
        binding_module._gate_count(JsonString(value="zero"))

    with pytest.raises(ValueError):
        binding_module._rule_metadata({"name": _integer(1)})
    with pytest.raises(ValueError):
        binding_module._rule_metadata({"tags": _array(_integer(1))})
    with pytest.raises(ValueError):
        binding_module._rule_metadata({"tags": _text("not-array")})


def test_prepared_capability_dispatch_for_ready_partial_missing_and_all_operator_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_id = EnvironmentExecutionId(value=UUID(int=77_001))
    match = _match(JsonString(value="ok"))
    observed = SimpleNamespace(
        snapshot=object(),
        observation=SimpleNamespace(execution_id=execution_id),
    )
    selector = SimpleNamespace(fingerprint=_fingerprint())
    control = cast("Any", object())
    prepared_predicates = cast("Any", binding_module._prepared_predicates)

    class FakeReady:
        def __init__(self, matches: tuple[SelectionMatch, ...]) -> None:
            self.matches = matches

    class FakePartial:
        def __init__(self, matches: tuple[SelectionMatch, ...]) -> None:
            self.matches = matches

    class Catalog:
        def __init__(self, value: object) -> None:
            self.value = value

        def predicate(self, *_args: object) -> object:
            return self.value

    monkeypatch.setattr(binding_module, "SelectionReady", FakeReady)
    monkeypatch.setattr(binding_module, "SelectionPartial", FakePartial)

    schema = schema_module.prepare_remote_schema(
        RemoteSchema(uri="https://example.test/schema"),
        b'{"type":"string"}',
        schema_module.DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
    )
    schema_operator = schema_module.schema_operator(schema)
    schema_rule = SimpleNamespace(operator=schema_operator, selector=selector)
    monkeypatch.setattr(
        binding_module,
        "select",
        lambda *_args, **_kwargs: SimpleNamespace(result=FakeReady((match,))),
    )
    assert prepared_predicates(
        cast("Any", Catalog("schema")),
        cast("Any", Catalog("regex")),
        cast("Any", schema_rule),
        cast("Any", observed),
        control,
        (),
        digest_module.DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
    ) == ("schema",)

    regex = regex_module.compile_regex(
        "ok",
        mode=RegexMode.SEARCH,
        negate=False,
    )
    regex_rule = SimpleNamespace(operator=regex.operator, selector=selector)
    monkeypatch.setattr(
        binding_module,
        "select",
        lambda *_args, **_kwargs: SimpleNamespace(result=FakePartial((match,))),
    )
    assert prepared_predicates(
        cast("Any", Catalog("schema")),
        cast("Any", Catalog("regex")),
        cast("Any", regex_rule),
        cast("Any", observed),
        control,
        (),
        digest_module.DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
    ) == ("regex",)

    digest_expected = hashlib.sha256(b"ok").hexdigest()
    expected_expression = _text(digest_expected)
    digest_operator = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=expected_expression,
    )
    digest_rule = SimpleNamespace(operator=digest_operator, selector=selector)
    assert (
        prepared_predicates(
            cast("Any", Catalog("schema")),
            cast("Any", Catalog("regex")),
            cast("Any", digest_rule),
            cast("Any", observed),
            control,
            (),
            digest_module.DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
        )
        == ()
    )

    bytes_match = _match(BytesValue(value=b"ok"))
    monkeypatch.setattr(
        binding_module,
        "select",
        lambda *_args, **_kwargs: SimpleNamespace(result=FakeReady((bytes_match,))),
    )
    expression = PreparedAssertionExpression(
        expression=expected_expression,
        result=PreparedOperandReady(value=JsonString(value=digest_expected)),
    )
    digest_result = prepared_predicates(
        cast("Any", Catalog("schema")),
        cast("Any", Catalog("regex")),
        cast("Any", digest_rule),
        cast("Any", observed),
        control,
        (expression,),
        digest_module.DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
    )
    assert digest_result[0].outcome is PreparedPredicateOutcome.SATISFIED

    monkeypatch.setattr(
        binding_module,
        "select",
        lambda *_args, **_kwargs: SimpleNamespace(result=object()),
    )
    assert (
        prepared_predicates(
            cast("Any", Catalog("schema")),
            cast("Any", Catalog("regex")),
            cast("Any", schema_rule),
            cast("Any", observed),
            control,
            (),
            digest_module.DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
        )
        == ()
    )
    equality = EqualityOperator(
        relation=EqualityRelation.EQUALS,
        expected=_text("ok"),
    )
    assert (
        prepared_predicates(
            cast("Any", Catalog("schema")),
            cast("Any", Catalog("regex")),
            cast("Any", SimpleNamespace(operator=equality, selector=selector)),
            cast("Any", observed),
            control,
            (),
            digest_module.DEFAULT_DIGEST_VALIDATION_LIMITS_V1,
        )
        == ()
    )
