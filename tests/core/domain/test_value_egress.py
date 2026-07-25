"""Named sensitive-source identities for destination-bound egress policy."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_egress import CONFIGURATION_SECRET_MARKER
from comparo.core.domain.value_egress import ENVIRONMENT_EGRESS_PROVIDER
from comparo.core.domain.value_egress import SECRET_NAMESPACE_EGRESS_PROVIDER
from comparo.core.domain.value_egress import ValueEgressSource
from comparo.core.domain.value_egress import ValueEgressSourceKind
from comparo.core.domain.value_egress import normalize_secret_egress_origins
from comparo.core.domain.value_egress import value_egress_origin
from comparo.core.domain.value_egress import value_egress_provider
from comparo.core.domain.value_egress import value_egress_source
from comparo.core.domain.value_references import SymbolPath


def test_value_egress_sources_bind_names_to_exact_provider_contracts() -> None:
    environment = value_egress_source(
        ValueEgressSourceKind.ENVIRONMENT,
        SymbolPath(segments=("API_TOKEN",)),
    )
    secret = value_egress_source(
        ValueEgressSourceKind.SECRET,
        SymbolPath(segments=("chat", "token")),
    )
    assert environment.provider == ENVIRONMENT_EGRESS_PROVIDER
    assert secret.provider == SECRET_NAMESPACE_EGRESS_PROVIDER
    assert environment.origin.value.startswith("env/")
    assert secret.origin.value.startswith("secret/")
    assert environment.origin == value_egress_origin(
        ValueEgressSourceKind.ENVIRONMENT,
        environment.symbol,
    )
    assert environment.origin != value_egress_origin(
        ValueEgressSourceKind.ENVIRONMENT,
        SymbolPath(segments=("OTHER_TOKEN",)),
    )


def test_value_egress_source_rejects_untyped_or_contradictory_coordinates() -> None:
    valid = value_egress_source(
        ValueEgressSourceKind.SECRET,
        SymbolPath(segments=("token",)),
    )
    with pytest.raises(TypeError, match="kind must be typed"):
        msgspec.structs.replace(valid, kind=cast("ValueEgressSourceKind", "secret"))
    with pytest.raises(TypeError, match="provider must be typed"):
        msgspec.structs.replace(valid, provider=cast("ValueProviderIdentity", object()))
    with pytest.raises(TypeError, match="symbol must be typed"):
        msgspec.structs.replace(valid, symbol=cast("SymbolPath", object()))
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(valid, provider=ENVIRONMENT_EGRESS_PROVIDER)
    with pytest.raises(TypeError, match="typed source kind"):
        value_egress_provider(cast("ValueEgressSourceKind", "secret"))
    with pytest.raises(TypeError, match="requires a SymbolPath"):
        value_egress_origin(ValueEgressSourceKind.SECRET, cast("SymbolPath", object()))
    with pytest.raises(TypeError, match="kind must be typed"):
        ValueEgressSource(
            kind=cast("ValueEgressSourceKind", object()),
            provider=SECRET_NAMESPACE_EGRESS_PROVIDER,
            symbol=SymbolPath(segments=("token",)),
        )
    with pytest.raises(ValueError, match="portable variable"):
        value_egress_source(
            ValueEgressSourceKind.ENVIRONMENT,
            SymbolPath(segments=("BAD-NAME",)),
        )
    with pytest.raises(ValueError, match="portable variable"):
        value_egress_source(
            ValueEgressSourceKind.ENVIRONMENT,
            SymbolPath(segments=("ONE", "TWO")),
        )


def test_secret_origin_normalization_replaces_only_the_configuration_marker() -> None:
    genuine = TaintOriginId(value="env/genuine")
    symbol = SymbolPath(segments=("chat", "token"))

    assert normalize_secret_egress_origins(
        (CONFIGURATION_SECRET_MARKER, genuine),
        symbol,
    ) == (
        genuine,
        value_egress_origin(ValueEgressSourceKind.SECRET, symbol),
    )
    with pytest.raises(TypeError, match="immutable typed origins"):
        normalize_secret_egress_origins(
            cast("tuple[TaintOriginId, ...]", [CONFIGURATION_SECRET_MARKER]),
            symbol,
        )
    with pytest.raises(TypeError, match="SymbolPath"):
        normalize_secret_egress_origins((), cast("SymbolPath", object()))
