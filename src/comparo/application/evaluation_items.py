"""Compile constrained relative selectors and predicates for diff pipelines."""

from __future__ import annotations

import hashlib

from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import ItemEqualityPredicate
from comparo.core.domain.selectors.items import ItemPredicateIdentity
from comparo.core.domain.selectors.items import ItemPredicateKind
from comparo.core.domain.selectors.items import ItemTypePredicate
from comparo.core.domain.selectors.model import derive_path_capabilities
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.values import JsonString
from comparo.core.selection import PathCompileReady
from comparo.core.selection import try_compile_json_pointer
from comparo.core.selection import try_compile_jsonpath


def compile_item_selector(
    expression: ValueExpression | None,
    input_contract: ValueContract,
    *,
    label: str,
) -> CompiledItemSelector:
    """Compile a JSON Pointer/JSONPath relative to one current derived item."""
    if type(input_contract) is not ValueContract or type(label) is not str or not label:
        raise TypeError("item selector compilation requires a contract and stable label")
    text = _text(expression)
    if text is None:
        raise ValueError("relative item selector path must be static text")
    path = _path(text)
    capabilities = derive_path_capabilities(path)
    item_input = ValueContract(
        kinds=input_contract.kinds,
        cardinality=SelectorCardinality.EXACTLY_ONE,
    )
    output = (
        item_input
        if type(path) is NoPath
        else ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=capabilities.cardinality,
        )
    )
    return CompiledItemSelector(
        fingerprint=_fingerprint(
            "comparo.item-selector.v1",
            label,
            text,
            ",".join(kind.value for kind in item_input.kinds),
        ),
        input=item_input,
        path=path,
        capabilities=capabilities,
        output=output,
    )


def compile_item_predicate(
    expression: ValueExpression | None,
    selector: CompiledItemSelector,
) -> CompiledItemPredicate:
    """Compile the finite pure filter predicate vocabulary."""
    if type(selector) is not CompiledItemSelector:
        raise TypeError("item predicate requires a compiled item selector")
    if type(expression) is not ObjectExpr:
        raise ValueError("item predicate must be a structured operation")
    fields = {member.name: member.expression for member in expression.members}
    name = _text(fields.get("name"))
    operator: ItemEqualityPredicate | ItemTypePredicate
    if name in {"equals", "notEquals"}:
        if set(fields) != {"name", "expected"}:
            raise ValueError("item equality predicate fields are invalid")
        kind = ItemPredicateKind.EQUALS if name == "equals" else ItemPredicateKind.NOT_EQUALS
        operator = ItemEqualityPredicate(expected=fields["expected"])
    elif name == "isType":
        if set(fields) != {"name", "expected"}:
            raise ValueError("item type predicate fields are invalid")
        expected = _text(fields["expected"])
        if expected is None:
            raise ValueError("item type predicate requires static text")
        kind = ItemPredicateKind.IS_TYPE
        operator = ItemTypePredicate(expected=ValueKind(expected))
    else:
        raise ValueError("unsupported item predicate")
    return CompiledItemPredicate(
        identity=ItemPredicateIdentity(namespace="comparo.core", kind=kind, version="1"),
        selector=selector,
        operator=operator,
    )


def _path(text: str) -> SelectorPath:
    if text == "":
        return NoPath()
    compiled = (
        try_compile_jsonpath(text) if text.startswith("$") else try_compile_json_pointer(text)
    )
    if type(compiled) is not PathCompileReady or type(compiled.path) not in {
        JsonPointerPath,
        JsonPathExpression,
    }:
        raise ValueError("relative item selector requires JSON Pointer or JSONPath syntax")
    return compiled.path


def _text(expression: ValueExpression | None) -> str | None:
    return (
        expression.value.value
        if type(expression) is LiteralExpr and type(expression.value) is JsonString
        else None
    )


def _fingerprint(*parts: str) -> Fingerprint:
    payload = "\x1f".join(parts).encode("utf-8")
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(payload).hexdigest()),
    )


__all__ = ("compile_item_predicate", "compile_item_selector")
