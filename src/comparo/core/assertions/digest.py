"""Prepared digest evidence validation without body storage access."""

from __future__ import annotations

from typing import cast

from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString

_ACCESS = SensitiveValueAccess(purpose="assertion.digest.validate")


def digest_matches(
    evidence: DigestEvidence,
    expected: object,
    operator: DigestOperator,
) -> bool:
    """Compare a prepared digest to its exactly bound typed operand."""
    if evidence.algorithm is not operator.algorithm:
        raise ValueError("prepared digest algorithm differs from its operator")
    actual_value = evidence.actual
    if is_sensitive_value(actual_value):
        actual = cast("BytesValue", actual_value.reveal(access=_ACCESS)).value
    else:
        actual = cast("BytesValue", actual_value).value
    if type(expected) is BytesValue:
        if len(expected.value) != len(actual):
            raise ValueError("digest operand has the wrong byte width")
        return actual == expected.value
    if type(expected) is JsonString:
        _validate_hex(expected.value, len(actual) * 2)
        return actual.hex() == expected.value
    raise TypeError("digest operand must be bytes or lowercase hexadecimal text")


def validate_digest_expected(expected: object, operator: DigestOperator) -> None:
    """Reject malformed digest operands before consulting a prepared capability cache."""
    width = 32 if operator.algorithm is DigestAlgorithm.SHA256 else 64
    if type(expected) is BytesValue:
        if len(expected.value) != width:
            raise ValueError("digest operand has the wrong byte width")
        return
    if type(expected) is JsonString:
        _validate_hex(expected.value, width * 2)
        return
    raise TypeError("digest operand must be bytes or lowercase hexadecimal text")


def _validate_hex(value: str, width: int) -> None:
    if len(value) != width or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("digest text must be exact lowercase hexadecimal")
