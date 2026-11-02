"""Canonical value-source identities used by destination-bound egress authority."""

from __future__ import annotations

import hashlib
import re
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.value_references import SymbolPath


class ValueEgressSourceKind(DomainStrEnum):
    """Closed initial value sources that an operator may authorize for HTTP egress."""

    ENVIRONMENT = "environment"
    SECRET = "secret"  # noqa: S105 - public source-category identifier


ENVIRONMENT_EGRESS_PROVIDER = ValueProviderIdentity(
    namespace="comparo",
    name="env-snapshot",
    version="1",
)
SECRET_NAMESPACE_EGRESS_PROVIDER = ValueProviderIdentity(
    namespace="comparo",
    name="secret-namespace",
    version="1",
)
CONFIGURATION_SECRET_MARKER = TaintOriginId(value="configuration/secret")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@final
class ValueEgressSource(msgspec.Struct, frozen=True, kw_only=True):
    """One named, non-secret value source admitted by an operator policy."""

    kind: ValueEgressSourceKind
    provider: ValueProviderIdentity
    symbol: SymbolPath

    def __post_init__(self) -> None:
        """Bind each source kind to its exact built-in provider contract."""
        if type(self.kind) is not ValueEgressSourceKind:
            raise TypeError("value egress source kind must be typed")
        if type(self.provider) is not ValueProviderIdentity:
            raise TypeError("value egress source provider must be typed")
        if type(self.symbol) is not SymbolPath:
            raise TypeError("value egress source symbol must be typed")
        expected = value_egress_provider(self.kind)
        if self.provider != expected:
            raise ValueError("value egress source provider contradicts its source kind")
        if self.kind is ValueEgressSourceKind.ENVIRONMENT and (
            len(self.symbol.segments) != 1
            or _ENVIRONMENT_NAME.fullmatch(self.symbol.segments[0]) is None
        ):
            raise ValueError("environment egress source must use one portable variable name")

    @property
    def origin(self) -> TaintOriginId:
        """Return the canonical sealed lineage identity for this source coordinate."""
        return value_egress_origin(self.kind, self.symbol)


def value_egress_provider(kind: ValueEgressSourceKind) -> ValueProviderIdentity:
    """Return the exact provider contract represented by one public policy source kind."""
    if type(kind) is not ValueEgressSourceKind:
        raise TypeError("value egress provider lookup requires a typed source kind")
    if kind is ValueEgressSourceKind.ENVIRONMENT:
        return ENVIRONMENT_EGRESS_PROVIDER
    return SECRET_NAMESPACE_EGRESS_PROVIDER


def value_egress_origin(kind: ValueEgressSourceKind, symbol: SymbolPath) -> TaintOriginId:
    """Derive a stable value-free lineage ID from an exact provider/name coordinate."""
    provider = value_egress_provider(kind)
    if type(symbol) is not SymbolPath:
        raise TypeError("value egress origin requires a SymbolPath")
    coordinate = (
        f"{provider.namespace}/{provider.name}/{provider.version}:{'.'.join(symbol.segments)}"
    )
    digest = hashlib.sha256(coordinate.encode()).hexdigest()[:24]
    prefix = "env" if kind is ValueEgressSourceKind.ENVIRONMENT else "secret"
    return TaintOriginId(value=f"{prefix}/{digest}")


def normalize_secret_egress_origins(
    origins: tuple[TaintOriginId, ...],
    symbol: SymbolPath,
) -> tuple[TaintOriginId, ...]:
    """Replace the configuration marker with one named secret origin at resolution."""
    if type(origins) is not tuple or any(type(origin) is not TaintOriginId for origin in origins):
        raise TypeError("secret egress normalization requires immutable typed origins")
    if type(symbol) is not SymbolPath:
        raise TypeError("secret egress normalization requires a SymbolPath")
    genuine = tuple(origin for origin in origins if origin != CONFIGURATION_SECRET_MARKER)
    return stable_taint_origin_union(
        genuine,
        (value_egress_origin(ValueEgressSourceKind.SECRET, symbol),),
    )


def value_egress_source(
    kind: ValueEgressSourceKind,
    symbol: SymbolPath,
) -> ValueEgressSource:
    """Construct a source with the exact provider identity fixed by its kind."""
    return ValueEgressSource(
        kind=kind,
        provider=value_egress_provider(kind),
        symbol=symbol,
    )


__all__ = (
    "CONFIGURATION_SECRET_MARKER",
    "ENVIRONMENT_EGRESS_PROVIDER",
    "SECRET_NAMESPACE_EGRESS_PROVIDER",
    "ValueEgressSource",
    "ValueEgressSourceKind",
    "normalize_secret_egress_origins",
    "value_egress_origin",
    "value_egress_provider",
    "value_egress_source",
)
