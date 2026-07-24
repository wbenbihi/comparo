"""Adversarial regressions for hardened enums and exact sensitive wrappers."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import msgspec
import pytest
from tests.core.domain.http.factories import provenance

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicPassword
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BasicWireEncoding
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId

_SECRET = "hostile-subclass-secret"


def _hostile_sensitive(payload: object) -> SensitiveValue[object]:
    """Construct a behavior-changing runtime subclass despite typing.final metadata."""
    hostile_type = type(
        "HostileSensitiveValue",
        (SensitiveValue,),
        {
            "payload_type": property(lambda _self: type(payload)),
            "__repr__": lambda _self: _SECRET,
            "__str__": lambda _self: _SECRET,
        },
    )
    return cast(
        "SensitiveValue[object]",
        hostile_type(payload, origins=(TaintOriginId(value="hostile-test"),)),
    )


def test_sensitive_receivers_reject_behavior_changing_subclasses_without_echo() -> None:
    hostile_header = _hostile_sensitive(HeaderText(value=_SECRET))
    hostile_body = _hostile_sensitive(BodyText(value=_SECRET))
    hostile_user = _hostile_sensitive(BasicUsername(value=_SECRET))
    hostile_path = _hostile_sensitive(DecodedPathSegment(value=_SECRET))
    password = SensitiveValue(
        BasicPassword(value="password"),
        origins=(TaintOriginId(value="password"),),
    )
    constructors: tuple[Callable[[], object], ...] = (
        lambda: HeaderField(
            name=HeaderName(value="x-context"),
            value=cast("HeaderText", hostile_header),
            source=HeaderSource.AUTHORED,
            provenance=provenance(),
        ),
        lambda: TextBody(value=cast("BodyText", hostile_body)),
        lambda: BasicAuthentication(
            username=cast("BasicUsername", hostile_user),
            password=password,
            wire_encoding=BasicWireEncoding.UTF_8,
        ),
        lambda: UrlPath(segments=(cast("DecodedPathSegment", hostile_path),)),
    )
    for construct in constructors:
        with pytest.raises(TypeError) as captured:
            construct()
        assert _SECRET not in str(captured.value)


def test_domain_enum_members_reject_ordinary_mutation_and_keep_wire_identity() -> None:
    member = HttpScheme.HTTPS
    original_hash = hash(member)
    original_wire = msgspec.json.encode(member)
    operations: tuple[Callable[[], object], ...] = (
        lambda: setattr(member, "custom", "changed"),
        lambda: setattr(member, "_value_", "changed"),
        lambda: setattr(member, "_name_", "CHANGED"),
        lambda: delattr(member, "_value_"),
    )
    for operation in operations:
        with pytest.raises(AttributeError, match="immutable"):
            operation()
    with pytest.raises(TypeError):
        member.__dict__["_value_"] = "changed"
    assert member.value == "https"
    assert hash(member) == original_hash
    assert msgspec.json.encode(member) == original_wire == b'"https"'

    uninitialized = str.__new__(DomainStrEnum, "uninitialized")
    with pytest.raises(AttributeError):
        DomainStrEnum.__delattr__(uninitialized, "missing")
