"""Schema, HTTP, digest, decode, and cardinality assertion operators."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.sources import BodyViewKind


@final
class CompiledSchema(msgspec.Struct, frozen=True, kw_only=True):
    """A pinned schema identity, never a live remote-fetch instruction."""

    fingerprint: Fingerprint
    resource: DefinitionRef | None = None
    remote_uri: NamedCapabilityTarget | None = None

    def __post_init__(self) -> None:
        """Require exactly one local or remote source coordinate."""
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("schema fingerprint must be a Fingerprint")
        if (self.resource is None) == (self.remote_uri is None):
            raise ValueError("compiled schema requires exactly one source coordinate")
        if self.resource is not None and (
            type(self.resource) is not DefinitionRef
            or self.resource.identity.kind is not ResourceKind.SCHEMA
        ):
            raise ValueError("local schema must reference a Schema resource")
        if self.remote_uri is not None and (
            type(self.remote_uri) is not NamedCapabilityTarget
            or self.remote_uri.kind is not CapabilityTargetKind.SCHEMA_SOURCE
            or not self.remote_uri.identifier.startswith(("http://", "https://"))
        ):
            raise ValueError("remote schema URI must be an exact public schema-source URL")


@final
class SchemaOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Non-mutating JSON Schema validation with explicit dialect behavior."""

    schema: CompiledSchema
    default_dialect: NamedCapabilityTarget
    assert_formats: bool = False

    def __post_init__(self) -> None:
        """Require compiled schema and explicit format behavior."""
        if type(self.schema) is not CompiledSchema:
            raise TypeError("schema operator requires a CompiledSchema")
        if (
            type(self.default_dialect) is not NamedCapabilityTarget
            or self.default_dialect.kind is not CapabilityTargetKind.SCHEMA_SOURCE
            or not self.default_dialect.identifier.startswith(("http://", "https://"))
        ):
            raise ValueError("schema default dialect must be an exact public schema-source URL")
        if type(self.assert_formats) is not bool:
            raise TypeError("schema format behavior must be a bool")


@final
class DecodeOperator(msgspec.Struct, frozen=True, kw_only=True):
    """An explicit body-view decodability predicate."""

    view: BodyViewKind

    def __post_init__(self) -> None:
        """Require a typed body view."""
        if type(self.view) is not BodyViewKind:
            raise TypeError("decode view must be a BodyViewKind")


class MediaRelation(DomainStrEnum):
    """Parsed HTTP media-type relations."""

    EXACT = "exact"
    SUBSET_PARAMETERS = "subset-parameters"
    IGNORE_PARAMETERS = "ignore-parameters"
    SUFFIX_MATCH = "suffix-match"


@final
class MediaTypeOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Comparison with a statically parsed media type or range."""

    relation: MediaRelation
    expected: MediaType

    def __post_init__(self) -> None:
        """Require a typed relation and syntactic type/subtype value."""
        if type(self.relation) is not MediaRelation:
            raise TypeError("media relation must be a MediaRelation")
        if type(self.expected) is not MediaType:
            raise TypeError("expected media value must be a MediaType")


@final
class TokenOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Membership in an applicable parsed HTTP token list."""

    token: MediaTypeTokenValue

    def __post_init__(self) -> None:
        """Require a non-empty ASCII token."""
        if type(self.token) is not MediaTypeTokenValue:
            raise TypeError("HTTP token must be a MediaTypeTokenValue")


class DigestAlgorithm(DomainStrEnum):
    """Initial streaming digest algorithms."""

    SHA256 = "sha256"
    SHA512 = "sha512"


@final
class DigestOperator(msgspec.Struct, frozen=True, kw_only=True):
    """Streaming byte digest comparison."""

    algorithm: DigestAlgorithm
    expected: ValueExpression

    def __post_init__(self) -> None:
        """Require explicit algorithm and resolvable digest operand."""
        if type(self.algorithm) is not DigestAlgorithm:
            raise TypeError("digest algorithm must be a DigestAlgorithm")
        if not is_value_expression(self.expected):
            raise TypeError("digest expected value must be a ValueExpression")


@final
class CardinalityOperator(msgspec.Struct, frozen=True, kw_only=True):
    """A predicate-free exact or bounded match-count constraint."""

    exact: Count | None = None
    minimum: Count | None = None
    maximum: Count | None = None

    def __post_init__(self) -> None:
        """Require unambiguous exact or range semantics."""
        for bound in (self.exact, self.minimum, self.maximum):
            if bound is not None and type(bound) is not Count:
                raise TypeError("cardinality bounds must be Count values")
        if self.exact is not None and (self.minimum is not None or self.maximum is not None):
            raise ValueError("exact cardinality cannot be combined with range bounds")
        if self.exact is None and self.minimum is None and self.maximum is None:
            raise ValueError("cardinality requires exact, minimum, or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum.value > self.maximum.value
        ):
            raise ValueError("cardinality minimum cannot exceed maximum")
