"""Observation-independent selectors and predicates over current derived items."""

from __future__ import annotations

import re
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.contracts import contract_accepts
from comparo.core.domain.selectors.contracts import expression_accepts
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.model import derive_path_capabilities
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.paths import is_selector_path
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality

_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")
_JSON_CONTAINERS = frozenset({ValueKind.ARRAY, ValueKind.OBJECT})


@final
class CompiledItemSelector(msgspec.Struct, frozen=True, kw_only=True):
    """A relative selector rooted at one current pipeline item, not an observation."""

    fingerprint: Fingerprint
    input: ValueContract
    path: SelectorPath
    capabilities: SelectorCapabilities
    output: ValueContract

    def __post_init__(self) -> None:
        """Require compatible typed input, path, output, and derived capabilities."""
        _require_item_selector_components(self)
        _require_item_selector_semantics(self)


def _require_item_selector_components(selector: CompiledItemSelector) -> None:
    """Validate the direct typed fields of an item selector."""
    if type(selector.fingerprint) is not Fingerprint:
        raise TypeError("item selector fingerprint must be a Fingerprint")
    if type(selector.input) is not ValueContract or type(selector.output) is not ValueContract:
        raise TypeError("item selector input and output must be ValueContract values")
    if not is_selector_path(selector.path):
        raise TypeError("item selector path has an unsupported type")
    if type(selector.capabilities) is not SelectorCapabilities:
        raise TypeError("item selector capabilities must be SelectorCapabilities")


def _require_item_selector_semantics(selector: CompiledItemSelector) -> None:
    """Validate path, capability, and output consistency."""
    if selector.input.cardinality is not SelectorCardinality.EXACTLY_ONE:
        raise ValueError("item selector input must describe exactly one current item")
    if type(selector.path) is HttpFieldPath:
        raise ValueError("HTTP field paths require an observation-rooted selector")
    if type(selector.path) in {JsonPointerPath, JsonPathExpression, StaticFieldPath} and not (
        contract_accepts(selector.input, _JSON_CONTAINERS)
    ):
        raise ValueError("item selector paths require object or array input")
    expected = derive_path_capabilities(selector.path)
    if selector.capabilities != expected:
        raise ValueError("item selector capabilities contradict the compiled path")
    if selector.output.cardinality is not selector.capabilities.cardinality:
        raise ValueError("item selector output cardinality contradicts its capabilities")
    if type(selector.path) is NoPath and selector.output != selector.input:
        raise ValueError("NoPath item selectors must preserve their input contract")


def is_singular_item_selector(selector: object) -> TypeGuard[CompiledItemSelector]:
    """Return whether compilation proves an item selector has at most one match."""
    return type(selector) is CompiledItemSelector and (
        selector.capabilities.cardinality is not SelectorCardinality.ZERO_OR_MORE
    )


class ItemPredicateKind(DomainStrEnum):
    """Initial pure predicate vocabulary for filtering current items."""

    EQUALS = "equals"
    NOT_EQUALS = "not-equals"
    IS_TYPE = "is-type"


@final
class ItemPredicateIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """Versioned identity for a pure current-item predicate."""

    namespace: str
    kind: ItemPredicateKind
    version: str

    def __post_init__(self) -> None:
        """Require a namespaced positive-version identity."""
        if type(self.namespace) is not str or _TOKEN_PATTERN.fullmatch(self.namespace) is None:
            raise ValueError("item predicate namespace must be a lowercase dotted token")
        if type(self.kind) is not ItemPredicateKind:
            raise TypeError("item predicate kind must be an ItemPredicateKind")
        if (
            type(self.version) is not str
            or not self.version.isascii()
            or not self.version.isdigit()
            or int(self.version) < 1
        ):
            raise ValueError("item predicate version must be a positive ASCII decimal")


@final
class ItemEqualityPredicate(msgspec.Struct, frozen=True, kw_only=True):
    """Strict equality or inequality against one compiled value expression."""

    expected: ValueExpression

    def __post_init__(self) -> None:
        """Require one resolvable operand."""
        if not is_value_expression(self.expected):
            raise TypeError("item equality expected value must be a ValueExpression")


@final
class ItemTypePredicate(msgspec.Struct, frozen=True, kw_only=True):
    """An exact current-item type predicate."""

    expected: ValueKind

    def __post_init__(self) -> None:
        """Require a concrete type rather than an unknown contract marker."""
        if type(self.expected) is not ValueKind:
            raise TypeError("item predicate expected type must be a ValueKind")
        if self.expected is ValueKind.UNKNOWN:
            raise ValueError("item type predicate cannot target unknown")


type ItemPredicateOperator = ItemEqualityPredicate | ItemTypePredicate


@final
class CompiledItemPredicate(msgspec.Struct, frozen=True, kw_only=True):
    """One pure predicate leaf over a current item selection."""

    identity: ItemPredicateIdentity
    selector: CompiledItemSelector
    operator: ItemPredicateOperator

    def __post_init__(self) -> None:
        """Bind exact predicate identity and reject known operand incompatibility."""
        if type(self.identity) is not ItemPredicateIdentity:
            raise TypeError("item predicate identity must be an ItemPredicateIdentity")
        if type(self.selector) is not CompiledItemSelector:
            raise TypeError("item predicate selector must be a CompiledItemSelector")
        if type(self.operator) not in {ItemEqualityPredicate, ItemTypePredicate}:
            raise TypeError("item predicate operator has an unsupported type")
        if self.identity.namespace != "comparo.core" or self.identity.version != "1":
            raise ValueError("built-in item predicates require comparo.core identity version 1")
        identity_matches = (
            type(self.operator) is ItemTypePredicate
            and self.identity.kind is ItemPredicateKind.IS_TYPE
        ) or (
            type(self.operator) is ItemEqualityPredicate
            and self.identity.kind in {ItemPredicateKind.EQUALS, ItemPredicateKind.NOT_EQUALS}
        )
        if not identity_matches:
            raise ValueError("item predicate identity does not match its typed operator")
        if (
            type(self.operator) is ItemEqualityPredicate
            and ValueKind.UNKNOWN not in self.selector.output.kinds
            and not expression_accepts(
                self.operator.expected,
                frozenset(self.selector.output.kinds),
            )
        ):
            raise ValueError("item predicate operand contradicts selector output types")


def is_item_predicate(value: object) -> TypeGuard[CompiledItemPredicate]:
    """Narrow an object to the closed compiled item predicate contract."""
    return type(value) is CompiledItemPredicate
