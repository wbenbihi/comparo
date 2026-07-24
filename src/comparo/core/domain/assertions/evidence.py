"""Typed, bounded evidence emitted by assertion operators."""

from __future__ import annotations

from typing import TypeGuard
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue

_ACCESS = SensitiveValueAccess(purpose="assertion.evidence.validate")


@final
class ApproximateEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """The exact rational delta and effective tolerance used by an approximate match."""

    delta: Ratio
    effective_tolerance: Ratio

    def __post_init__(self) -> None:
        """Require exact canonical non-negative rationals."""
        if type(self.delta) is not Ratio or type(self.effective_tolerance) is not Ratio:
            raise TypeError("approximate evidence requires exact Ratio values")
        if self.delta.numerator < 0 or self.effective_tolerance.numerator < 0:
            raise ValueError("approximate evidence magnitudes cannot be negative")


@final
class SchemaViolationLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One retained JSON Schema violation coordinate without payload text."""

    instance: JsonPointer
    schema: JsonPointer

    def __post_init__(self) -> None:
        """Require canonical instance and schema pointers."""
        if type(self.instance) is not JsonPointer or type(self.schema) is not JsonPointer:
            raise TypeError("schema violation locations require JsonPointer values")


@final
class SchemaValidationEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Exact violation counts with an independently bounded location sample."""

    violation_count: Count
    retained: tuple[SchemaViolationLocation, ...] = ()
    omitted: Count = Count(value=0)

    def __post_init__(self) -> None:
        """Prevent retained and omitted schema evidence from contradicting the exact count."""
        if type(self.violation_count) is not Count or type(self.omitted) is not Count:
            raise TypeError("schema evidence counts must use Count values")
        if type(self.retained) is not tuple or any(
            type(item) is not SchemaViolationLocation for item in self.retained
        ):
            raise TypeError("schema evidence locations must be an immutable typed tuple")
        if self.violation_count.value != len(self.retained) + self.omitted.value:
            raise ValueError("schema evidence must exactly cover its violation count")
        if len(set(self.retained)) != len(self.retained):
            raise ValueError("schema evidence locations must be unique")


type DigestBytes = BytesValue | SensitiveValue[BytesValue]


@final
class DigestEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """The computed digest, sealed whenever its source body is sensitive."""

    algorithm: DigestAlgorithm
    actual: DigestBytes

    def __post_init__(self) -> None:
        """Require the exact digest width and a closed public-or-sealed carrier."""
        if type(self.algorithm) is not DigestAlgorithm:
            raise TypeError("digest evidence requires a DigestAlgorithm")
        if is_sensitive_value(self.actual):
            if self.actual.payload_type is not BytesValue:
                raise TypeError("sensitive digest evidence must seal BytesValue")
            payload = self.actual.reveal(access=_ACCESS)
            size = len(cast("BytesValue", payload).value)
        elif type(self.actual) is BytesValue:
            size = len(self.actual.value)
        else:
            raise TypeError("digest evidence requires public or sealed BytesValue")
        expected = 32 if self.algorithm is DigestAlgorithm.SHA256 else 64
        if size != expected:
            raise ValueError("digest evidence has the wrong width for its algorithm")


type AssertionOperatorEvidence = ApproximateEvidence | SchemaValidationEvidence | DigestEvidence
type AssertionOperatorEvidenceValue = (
    AssertionOperatorEvidence | SensitiveValue[AssertionOperatorEvidence]
)


def is_assertion_operator_evidence(
    value: object,
) -> TypeGuard[AssertionOperatorEvidenceValue]:
    """Recognize the closed assertion operator evidence union."""
    if type(value) in {ApproximateEvidence, SchemaValidationEvidence, DigestEvidence}:
        return True
    return is_sensitive_value(value) and value.payload_type in {
        ApproximateEvidence,
        SchemaValidationEvidence,
        DigestEvidence,
    }
