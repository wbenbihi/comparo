"""Versioned typed built-in assertion operator configurations."""

from __future__ import annotations

import re
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.extended_operators import TokenOperator
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.contracts import is_statically_negative_number

_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")
BUILTIN_ASSERTION_NAMESPACE = "comparo.core"
BUILTIN_ASSERTION_VERSION = "1"


class AssertionOperatorKind(DomainStrEnum):
    """Stable built-in operation names."""

    EQUALITY = "equality"
    MEMBERSHIP = "membership"
    IS_TYPE = "is-type"
    ORDERING = "ordering"
    BETWEEN = "between"
    APPROXIMATELY_EQUALS = "approximately-equals"
    STRING = "string"
    REGEX = "regex"
    CONTAINS = "contains"
    KEY_SET = "key-set"
    LENGTH = "length"
    CONFORMS_TO = "conforms-to"
    DECODES_AS = "decodes-as"
    MEDIA_TYPE = "media-type"
    CONTAINS_TOKEN = "contains-token"  # noqa: S105
    DIGEST_EQUALS = "digest-equals"
    CARDINALITY = "cardinality"


@final
class AssertionOperatorIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """A namespaced positive-version operation identity."""

    namespace: str
    kind: AssertionOperatorKind
    version: str

    def __post_init__(self) -> None:
        """Reject ambiguous and unversioned identities."""
        if type(self.namespace) is not str or _TOKEN_PATTERN.fullmatch(self.namespace) is None:
            raise ValueError("operator namespace must be a lowercase dotted token")
        if type(self.kind) is not AssertionOperatorKind:
            raise TypeError("operator kind must be an AssertionOperatorKind")
        if (
            type(self.version) is not str
            or not self.version.isascii()
            or not self.version.isdigit()
        ):
            raise ValueError("operator version must be an ASCII decimal version")
        if int(self.version) < 1:
            raise ValueError("operator version must be positive")


class EqualityRelation(DomainStrEnum):
    """Strict structural equality relations."""

    EQUALS = "equals"
    NOT_EQUALS = "not-equals"


@final
class EqualityOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Strict equality without conversion or missing/null equivalence."""

    relation: EqualityRelation
    expected: ValueExpression

    def __post_init__(self) -> None:
        """Require a typed relation and compiled operand."""
        if type(self.relation) is not EqualityRelation:
            raise TypeError("equality relation must be an EqualityRelation")
        _require_expression(self.expected)


class MembershipRelation(DomainStrEnum):
    """Strict immutable-choice membership relations."""

    IN = "in"
    NOT_IN = "not-in"


@final
class MembershipOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Membership in a non-empty immutable tuple of exact operands."""

    relation: MembershipRelation
    choices: tuple[ValueExpression, ...]

    def __post_init__(self) -> None:
        """Require typed relation and operands."""
        if type(self.relation) is not MembershipRelation:
            raise TypeError("membership relation must be a MembershipRelation")
        _require_expressions(self.choices)


class ExactValueType(DomainStrEnum):
    """Types visible to strict operator dispatch."""

    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"
    BYTES = "bytes"
    DURATION = "duration"
    SIZE = "size"
    INSTANT = "instant"


@final
class TypeOperator(msgspec.Struct, frozen=True, kw_only=True):
    """An exact domain-type predicate."""

    expected: ExactValueType

    def __post_init__(self) -> None:
        """Require a supported exact type."""
        if type(self.expected) is not ExactValueType:
            raise TypeError("expected type must be an ExactValueType")


class OrderingRelation(DomainStrEnum):
    """Exact numeric, duration, size, and instant relations."""

    LESS = "less"
    LESS_OR_EQUAL = "less-or-equal"
    GREATER = "greater"
    GREATER_OR_EQUAL = "greater-or-equal"


@final
class OrderingOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Typed ordering with no implicit string collation."""

    relation: OrderingRelation
    expected: ValueExpression

    def __post_init__(self) -> None:
        """Require a typed relation and operand."""
        if type(self.relation) is not OrderingRelation:
            raise TypeError("ordering relation must be an OrderingRelation")
        _require_expression(self.expected)


@final
class BetweenOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Typed range comparison with explicit inclusive boundaries."""

    lower: ValueExpression
    upper: ValueExpression
    include_lower: bool
    include_upper: bool

    def __post_init__(self) -> None:
        """Require typed operands and exact boundary flags."""
        _require_expression(self.lower)
        _require_expression(self.upper)
        if type(self.include_lower) is not bool or type(self.include_upper) is not bool:
            raise TypeError("between inclusion flags must be bool values")


@final
class ApproximateOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Exact numeric comparison with explicit absolute and/or relative tolerance."""

    expected: ValueExpression
    absolute_tolerance: ValueExpression | None = None
    relative_tolerance: ValueExpression | None = None

    def __post_init__(self) -> None:
        """Require an operand and at least one explicit tolerance."""
        _require_expression(self.expected)
        if self.absolute_tolerance is None and self.relative_tolerance is None:
            raise ValueError("approximate comparison requires at least one tolerance")
        for tolerance in (self.absolute_tolerance, self.relative_tolerance):
            if tolerance is not None:
                _require_expression(tolerance)
                if is_statically_negative_number(tolerance):
                    raise ValueError("approximate tolerances cannot be negative")


class StringRelation(DomainStrEnum):
    """Literal string relations without implicit normalization."""

    CONTAINS = "contains"
    NOT_CONTAINS = "not-contains"
    STARTS_WITH = "starts-with"
    ENDS_WITH = "ends-with"


@final
class StringOperator(msgspec.Struct, frozen=True, kw_only=True):
    """A literal case-sensitive string operation."""

    relation: StringRelation
    expected: ValueExpression

    def __post_init__(self) -> None:
        """Require a typed relation and operand."""
        if type(self.relation) is not StringRelation:
            raise TypeError("string relation must be a StringRelation")
        _require_expression(self.expected)


class ContainsMode(DomainStrEnum):
    """Explicit collection containment quantification."""

    ONE = "one"
    ALL = "all"
    ANY = "any"


@final
class ContainsOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Type-defined containment against explicit operands."""

    mode: ContainsMode
    expected: tuple[ValueExpression, ...]

    def __post_init__(self) -> None:
        """Require typed mode and non-empty operands."""
        if type(self.mode) is not ContainsMode:
            raise TypeError("contains mode must be a ContainsMode")
        _require_expressions(self.expected)
        if self.mode is ContainsMode.ONE and len(self.expected) != 1:
            raise ValueError("contains one requires exactly one expected value")


class KeySetRelation(DomainStrEnum):
    """Explicit object-key set relations."""

    HAS = "has"
    HAS_ALL = "has-all"
    HAS_ONLY = "has-only"


@final
class KeySetOperator(msgspec.Struct, frozen=True, kw_only=True):
    """An object-key predicate over immutable unique names."""

    relation: KeySetRelation
    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require typed relation and non-empty unique keys."""
        if type(self.relation) is not KeySetRelation:
            raise TypeError("key-set relation must be a KeySetRelation")
        if type(self.keys) is not tuple or not self.keys:
            raise ValueError("key-set keys must be a non-empty immutable tuple")
        if any(type(key) is not str for key in self.keys):
            raise TypeError("key-set keys must be text")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("key-set keys must be unique")


class CountRelation(DomainStrEnum):
    """Exact count relations used by length and cardinality."""

    EQUALS = "equals"
    AT_LEAST = "at-least"
    AT_MOST = "at-most"


@final
class LengthOperator(msgspec.Struct, frozen=True, kw_only=True):
    """A type-defined length predicate."""

    relation: CountRelation
    expected: Count

    def __post_init__(self) -> None:
        """Require typed relation and exact count."""
        if type(self.relation) is not CountRelation or type(self.expected) is not Count:
            raise TypeError("length requires a CountRelation and Count")


type AssertionOperator = (
    EqualityOperator
    | MembershipOperator
    | TypeOperator
    | OrderingOperator
    | BetweenOperator
    | ApproximateOperator
    | StringOperator
    | RegexOperator
    | ContainsOperator
    | KeySetOperator
    | LengthOperator
    | SchemaOperator
    | DecodeOperator
    | MediaTypeOperator
    | TokenOperator
    | DigestOperator
    | CardinalityOperator
)


def is_assertion_operator(value: object) -> TypeGuard[AssertionOperator]:
    """Narrow to the closed typed built-in operator union."""
    return type(value) in {
        EqualityOperator,
        MembershipOperator,
        TypeOperator,
        OrderingOperator,
        BetweenOperator,
        ApproximateOperator,
        StringOperator,
        RegexOperator,
        ContainsOperator,
        KeySetOperator,
        LengthOperator,
        SchemaOperator,
        DecodeOperator,
        MediaTypeOperator,
        TokenOperator,
        DigestOperator,
        CardinalityOperator,
    }


def operator_kind(operator: AssertionOperator) -> AssertionOperatorKind:  # noqa: C901
    """Return the operation identity required by one typed configuration."""
    if type(operator) is EqualityOperator:
        return AssertionOperatorKind.EQUALITY
    if type(operator) is MembershipOperator:
        return AssertionOperatorKind.MEMBERSHIP
    if type(operator) is TypeOperator:
        return AssertionOperatorKind.IS_TYPE
    if type(operator) is OrderingOperator:
        return AssertionOperatorKind.ORDERING
    if type(operator) is BetweenOperator:
        return AssertionOperatorKind.BETWEEN
    if type(operator) is ApproximateOperator:
        return AssertionOperatorKind.APPROXIMATELY_EQUALS
    if type(operator) is StringOperator:
        return AssertionOperatorKind.STRING
    if type(operator) is RegexOperator:
        return AssertionOperatorKind.REGEX
    if type(operator) is ContainsOperator:
        return AssertionOperatorKind.CONTAINS
    if type(operator) is KeySetOperator:
        return AssertionOperatorKind.KEY_SET
    if type(operator) is LengthOperator:
        return AssertionOperatorKind.LENGTH
    if type(operator) is SchemaOperator:
        return AssertionOperatorKind.CONFORMS_TO
    if type(operator) is DecodeOperator:
        return AssertionOperatorKind.DECODES_AS
    if type(operator) is MediaTypeOperator:
        return AssertionOperatorKind.MEDIA_TYPE
    if type(operator) is TokenOperator:
        return AssertionOperatorKind.CONTAINS_TOKEN
    if type(operator) is DigestOperator:
        return AssertionOperatorKind.DIGEST_EQUALS
    if type(operator) is CardinalityOperator:
        return AssertionOperatorKind.CARDINALITY
    raise TypeError("unsupported assertion operator")


def require_operator(identity: AssertionOperatorIdentity, operator: AssertionOperator) -> None:
    """Reject an identity/configuration mismatch at direct construction boundaries."""
    if type(identity) is not AssertionOperatorIdentity:
        raise TypeError("operator identity must be an AssertionOperatorIdentity")
    if not is_assertion_operator(operator):
        raise TypeError("assertion operator has an unsupported type")
    if (
        identity.namespace != BUILTIN_ASSERTION_NAMESPACE
        or identity.version != BUILTIN_ASSERTION_VERSION
    ):
        raise ValueError("built-in assertion operators require comparo.core identity version 1")
    if identity.kind is not operator_kind(operator):
        raise ValueError("operator identity does not match its typed configuration")


def operator_uses_match_predicate(operator: AssertionOperator) -> bool:
    """Return whether evaluation produces one predicate fact per inspected match."""
    if not is_assertion_operator(operator):
        raise TypeError("assertion operator has an unsupported type")
    return type(operator) is not CardinalityOperator


def _require_expression(value: ValueExpression) -> None:
    if not is_value_expression(value):
        raise TypeError("operator operand must be a ValueExpression")


def _require_expressions(values: tuple[ValueExpression, ...]) -> None:
    if type(values) is not tuple:
        raise TypeError("operator operands must be an immutable tuple")
    if not values:
        raise ValueError("operator operands must not be empty")
    if any(not is_value_expression(value) for value in values):
        raise TypeError("operator operands must contain ValueExpression values")
