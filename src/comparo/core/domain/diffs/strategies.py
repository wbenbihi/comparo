"""Typed reducers, pairing strategies, and comparators for diff pipelines."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import require_operation
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.selectors.contracts import is_statically_negative_number
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import is_singular_item_selector


class ReducerKind(StrEnum):
    """Finite semantic reducers, independent of wire chunking."""

    FIRST = "first"
    LAST = "last"
    COUNT = "count"
    CONCATENATE = "concatenate"
    SUM = "sum"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    COLLECT = "collect"
    SET = "set"
    MULTISET = "multiset"
    DISTINCT = "distinct"


class EmptyInputMode(StrEnum):
    """Explicit empty-input reducer behavior."""

    ERROR = "error"
    UNAVAILABLE = "unavailable"
    RETURN_ZERO = "return-zero"
    RETURN_EMPTY_TEXT = "return-empty-text"
    RETURN_EMPTY_COLLECTION = "return-empty-collection"


@final
class Reducer(msgspec.Struct, frozen=True, kw_only=True):
    """A semantic reducer with explicit empty and concatenate behavior."""

    identity: DiffOperationIdentity
    kind: ReducerKind
    empty: EmptyInputMode
    separator: ValueExpression | None = None

    def __post_init__(self) -> None:
        """Require matching identity and separator exactly for concatenation."""
        if type(self.kind) is not ReducerKind:
            raise TypeError("reducer kind must be a ReducerKind")
        require_operation(self.identity, DiffOperationFamily.REDUCER, self.kind.value)
        if type(self.empty) is not EmptyInputMode:
            raise TypeError("reducer empty mode must be an EmptyInputMode")
        permitted_empty = {
            EmptyInputMode.ERROR,
            EmptyInputMode.UNAVAILABLE,
        }
        if self.kind in {ReducerKind.COUNT, ReducerKind.SUM}:
            permitted_empty.add(EmptyInputMode.RETURN_ZERO)
        elif self.kind is ReducerKind.CONCATENATE:
            permitted_empty.add(EmptyInputMode.RETURN_EMPTY_TEXT)
        elif self.kind in {
            ReducerKind.COLLECT,
            ReducerKind.SET,
            ReducerKind.MULTISET,
            ReducerKind.DISTINCT,
        }:
            permitted_empty.add(EmptyInputMode.RETURN_EMPTY_COLLECTION)
        if self.empty not in permitted_empty:
            raise ValueError("reducer empty behavior has no identity for this reducer kind")
        if (self.kind is ReducerKind.CONCATENATE) != (self.separator is not None):
            raise ValueError("exactly concatenate reducers require a separator")
        if self.separator is not None and not is_value_expression(self.separator):
            raise TypeError("concatenate separator must be a ValueExpression")


class DuplicateKeyMode(StrEnum):
    """Explicit behavior for duplicate keys in key-producing stages."""

    ERROR = "error"
    INCOMPARABLE = "incomparable"
    KEEP_FIRST = "keep-first"
    KEEP_LAST = "keep-last"
    COLLECT = "collect"


@final
class GroupByReducer(msgspec.Struct, frozen=True, kw_only=True):
    """Group semantic values by a statically singular scalar key."""

    identity: DiffOperationIdentity
    key: CompiledItemSelector
    empty: EmptyInputMode
    duplicate_keys: DuplicateKeyMode

    def __post_init__(self) -> None:
        """Require matching identity and typed key selector."""
        require_operation(self.identity, DiffOperationFamily.REDUCER, "group-by")
        if type(self.key) is not CompiledItemSelector:
            raise TypeError("group-by key must be a CompiledItemSelector")
        if not is_singular_item_selector(self.key):
            raise ValueError("group-by key selector must be statically singular")
        if type(self.empty) is not EmptyInputMode:
            raise TypeError("group-by empty behavior must be an EmptyInputMode")
        if self.empty not in {
            EmptyInputMode.ERROR,
            EmptyInputMode.UNAVAILABLE,
            EmptyInputMode.RETURN_EMPTY_COLLECTION,
        }:
            raise ValueError("group-by requires an object-compatible empty behavior")
        if type(self.duplicate_keys) is not DuplicateKeyMode:
            raise TypeError("group-by duplicate behavior must be a DuplicateKeyMode")
        if self.duplicate_keys not in {DuplicateKeyMode.ERROR, DuplicateKeyMode.COLLECT}:
            raise ValueError("group-by duplicate keys must error or collect")


@final
class ObjectFromEntriesReducer(msgspec.Struct, frozen=True, kw_only=True):
    """Build an object from independently selected singular key/value entries."""

    identity: DiffOperationIdentity
    key: CompiledItemSelector
    value: CompiledItemSelector
    empty: EmptyInputMode
    duplicate_keys: DuplicateKeyMode

    def __post_init__(self) -> None:
        """Require singular selectors and explicit empty/duplicate semantics."""
        require_operation(self.identity, DiffOperationFamily.REDUCER, "object-from-entries")
        if (
            type(self.key) is not CompiledItemSelector
            or type(self.value) is not CompiledItemSelector
        ):
            raise TypeError("object-from-entries requires CompiledItemSelector values")
        if not is_singular_item_selector(self.key) or not is_singular_item_selector(self.value):
            raise ValueError("object-from-entries key and value selectors must be singular")
        if type(self.empty) is not EmptyInputMode:
            raise TypeError("object-from-entries empty behavior must be an EmptyInputMode")
        if self.empty not in {
            EmptyInputMode.ERROR,
            EmptyInputMode.UNAVAILABLE,
            EmptyInputMode.RETURN_EMPTY_COLLECTION,
        }:
            raise ValueError("object-from-entries requires an object-compatible empty behavior")
        if type(self.duplicate_keys) is not DuplicateKeyMode:
            raise TypeError("object duplicate behavior must be a DuplicateKeyMode")
        if self.duplicate_keys not in {
            DuplicateKeyMode.ERROR,
            DuplicateKeyMode.KEEP_FIRST,
            DuplicateKeyMode.KEEP_LAST,
        }:
            raise ValueError("object-from-entries requires explicit object duplicate handling")


type DiffReducer = Reducer | GroupByReducer | ObjectFromEntriesReducer


@final
class SingularPairing(msgspec.Struct, frozen=True, kw_only=True):
    """Pair two statically singular derived values."""

    identity: DiffOperationIdentity

    def __post_init__(self) -> None:
        """Require matching identity."""
        require_operation(self.identity, DiffOperationFamily.PAIRING, "singular")


@final
class OrderedPairing(msgspec.Struct, frozen=True, kw_only=True):
    """Pair by position without sorting or fallback."""

    identity: DiffOperationIdentity

    def __post_init__(self) -> None:
        """Require matching identity."""
        require_operation(self.identity, DiffOperationFamily.PAIRING, "ordered")


@final
class KeyedPairing(msgspec.Struct, frozen=True, kw_only=True):
    """Pair by independent singular keys, never positional fallback."""

    identity: DiffOperationIdentity
    baseline_key: CompiledItemSelector
    candidate_key: CompiledItemSelector
    duplicate_keys: DuplicateKeyMode

    def __post_init__(self) -> None:
        """Require typed keys and explicit duplicate behavior."""
        require_operation(self.identity, DiffOperationFamily.PAIRING, "keyed")
        if type(self.baseline_key) is not CompiledItemSelector:
            raise TypeError("baseline key must be a CompiledItemSelector")
        if type(self.candidate_key) is not CompiledItemSelector:
            raise TypeError("candidate key must be a CompiledItemSelector")
        if not is_singular_item_selector(self.baseline_key) or not is_singular_item_selector(
            self.candidate_key
        ):
            raise ValueError("keyed pairing selectors must be statically singular")
        if type(self.duplicate_keys) is not DuplicateKeyMode:
            raise TypeError("duplicate-key mode must be a DuplicateKeyMode")
        if self.duplicate_keys not in {
            DuplicateKeyMode.ERROR,
            DuplicateKeyMode.INCOMPARABLE,
        }:
            raise ValueError("keyed pairing duplicate keys must error or be incomparable")


class UnorderedPairingKind(StrEnum):
    """Order-insensitive identity models."""

    SET = "set"
    MULTISET = "multiset"


@final
class UnorderedPairing(msgspec.Struct, frozen=True, kw_only=True):
    """Pair by exact value uniqueness or multiplicity."""

    identity: DiffOperationIdentity
    kind: UnorderedPairingKind

    def __post_init__(self) -> None:
        """Require matching identity and explicit multiplicity behavior."""
        if type(self.kind) is not UnorderedPairingKind:
            raise TypeError("unordered pairing kind must be an UnorderedPairingKind")
        require_operation(self.identity, DiffOperationFamily.PAIRING, self.kind.value)


type DiffPairing = SingularPairing | OrderedPairing | KeyedPairing | UnorderedPairing


@final
class ExactComparator(msgspec.Struct, frozen=True, kw_only=True):
    """Strict typed structural equality."""

    identity: DiffOperationIdentity

    def __post_init__(self) -> None:
        """Require matching identity."""
        require_operation(self.identity, DiffOperationFamily.COMPARATOR, "exact")


@final
class IgnoreComparator(msgspec.Struct, frozen=True, kw_only=True):
    """Emit acknowledged ignored facts rather than equality."""

    identity: DiffOperationIdentity

    def __post_init__(self) -> None:
        """Require matching identity."""
        require_operation(self.identity, DiffOperationFamily.COMPARATOR, "ignore")


class CompatibilityKind(StrEnum):
    """Non-equality structural compatibility relations."""

    TYPE = "type"
    JSON_SHAPE = "json-shape"
    SUBSET = "subset"
    SUPERSET = "superset"


@final
class CompatibilityComparator(msgspec.Struct, frozen=True, kw_only=True):
    """Compare one explicit compatibility relation."""

    identity: DiffOperationIdentity
    kind: CompatibilityKind

    def __post_init__(self) -> None:
        """Require matching identity and relation."""
        if type(self.kind) is not CompatibilityKind:
            raise TypeError("compatibility kind must be a CompatibilityKind")
        require_operation(self.identity, DiffOperationFamily.COMPARATOR, self.kind.value)


@final
class NumericToleranceComparator(msgspec.Struct, frozen=True, kw_only=True):
    """Exact numeric comparison with absolute and/or relative tolerance."""

    identity: DiffOperationIdentity
    absolute_tolerance: ValueExpression | None = None
    relative_tolerance: ValueExpression | None = None

    def __post_init__(self) -> None:
        """Require at least one explicit tolerance."""
        require_operation(self.identity, DiffOperationFamily.COMPARATOR, "numeric-tolerance")
        if self.absolute_tolerance is None and self.relative_tolerance is None:
            raise ValueError("numeric tolerance requires at least one tolerance")
        for tolerance in (self.absolute_tolerance, self.relative_tolerance):
            if tolerance is not None and not is_value_expression(tolerance):
                raise TypeError("numeric tolerance must be a ValueExpression")
            if tolerance is not None and is_statically_negative_number(tolerance):
                raise ValueError("numeric tolerances cannot be negative")


class CollectionComparisonKind(StrEnum):
    """Collection relations with explicit order and multiplicity."""

    ORDERED = "ordered"
    SET = "set"
    MULTISET = "multiset"
    KEYED_OBJECTS = "keyed-objects"


@final
class CollectionComparator(msgspec.Struct, frozen=True, kw_only=True):
    """Compare collections under one explicit identity model."""

    identity: DiffOperationIdentity
    kind: CollectionComparisonKind

    def __post_init__(self) -> None:
        """Require matching identity and collection relation."""
        if type(self.kind) is not CollectionComparisonKind:
            raise TypeError("collection comparison kind must be a CollectionComparisonKind")
        require_operation(self.identity, DiffOperationFamily.COMPARATOR, self.kind.value)


class TextPredicateRelation(StrEnum):
    """Directed literal text relations with no hidden normalization."""

    BASELINE_CONTAINS_CANDIDATE = "baseline-contains-candidate"
    CANDIDATE_CONTAINS_BASELINE = "candidate-contains-baseline"
    CANDIDATE_STARTS_WITH_BASELINE = "candidate-starts-with-baseline"
    CANDIDATE_ENDS_WITH_BASELINE = "candidate-ends-with-baseline"


@final
class TextPredicateComparator(msgspec.Struct, frozen=True, kw_only=True):
    """Compare two text values under one explicit directed predicate."""

    identity: DiffOperationIdentity
    relation: TextPredicateRelation

    def __post_init__(self) -> None:
        """Require the exact built-in identity and directed relation."""
        require_operation(self.identity, DiffOperationFamily.COMPARATOR, "text-predicate")
        if type(self.relation) is not TextPredicateRelation:
            raise TypeError("text predicate relation must be a TextPredicateRelation")


type DiffComparator = (
    ExactComparator
    | IgnoreComparator
    | CompatibilityComparator
    | NumericToleranceComparator
    | CollectionComparator
    | TextPredicateComparator
)


def is_diff_reducer(value: object) -> TypeGuard[DiffReducer]:
    """Narrow to the closed reducer union."""
    return type(value) in {Reducer, GroupByReducer, ObjectFromEntriesReducer}


def is_diff_pairing(value: object) -> TypeGuard[DiffPairing]:
    """Narrow to the closed pairing union."""
    return type(value) in {SingularPairing, OrderedPairing, KeyedPairing, UnorderedPairing}


def is_diff_comparator(value: object) -> TypeGuard[DiffComparator]:
    """Narrow to the closed comparator union."""
    return type(value) in {
        ExactComparator,
        IgnoreComparator,
        CompatibilityComparator,
        NumericToleranceComparator,
        CollectionComparator,
        TextPredicateComparator,
    }
