"""Bounded digest preparation for the pure assertion kernel."""

from __future__ import annotations

import hashlib
from typing import final

import msgspec

from comparo.core.assertions.bindings import capability_implementation
from comparo.core.assertions.bindings import operands_fingerprint
from comparo.core.assertions.bindings import operation_fingerprint
from comparo.core.assertions.bindings import selected_fingerprint
from comparo.core.assertions.digest import digest_matches
from comparo.core.assertions.digest import validate_digest_expected
from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedOperandReady
from comparo.core.assertions.inputs import PreparedPredicate
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.values import BytesValue

_ACCESS = SensitiveValueAccess(purpose="digest.validation.prepare")
_INPUT_INVALID = DiagnosticCode(value="digest.input.invalid")
_INPUT_LIMIT = DiagnosticCode(value="digest.input.limit-exceeded")
_OPERAND_INVALID = DiagnosticCode(value="digest.operand.invalid")


@final
class DigestValidationLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned maximum bytes hashed for one assertion match."""

    maximum_input_bytes: int

    def __post_init__(self) -> None:
        """Require one explicit positive exact integer ceiling."""
        if type(self.maximum_input_bytes) is not int or self.maximum_input_bytes < 1:
            raise ValueError("digest validation limit must be a positive exact integer")


DEFAULT_DIGEST_VALIDATION_LIMITS_V1 = DigestValidationLimits(
    maximum_input_bytes=64 * 1024 * 1024,
)


def prepare_digest_predicate(
    operator: DigestOperator,
    match: SelectionMatch,
    operand: PreparedOperandReady,
    execution_id: EnvironmentExecutionId,
    selector_fingerprint: Fingerprint,
    limits: DigestValidationLimits,
) -> PreparedPredicate:
    """Hash one selected byte value and bind exact evidence and operand identity."""
    selected = selected_fingerprint(match)
    operands = operands_fingerprint((operand.value,))
    raw = match.value.reveal(access=_ACCESS) if is_sensitive_value(match.value) else match.value
    if type(raw) is not BytesValue:
        return _failure(
            operator,
            match,
            execution_id,
            selector_fingerprint,
            selected,
            operands,
            _INPUT_INVALID,
        )
    if len(raw.value) > limits.maximum_input_bytes:
        return _failure(
            operator,
            match,
            execution_id,
            selector_fingerprint,
            selected,
            operands,
            _INPUT_LIMIT,
        )
    expected = (
        operand.value.reveal(access=_ACCESS) if is_sensitive_value(operand.value) else operand.value
    )
    try:
        validate_digest_expected(expected, operator)
    except (TypeError, ValueError):
        return _failure(
            operator,
            match,
            execution_id,
            selector_fingerprint,
            selected,
            operands,
            _OPERAND_INVALID,
        )
    digest = hashlib.sha256(raw.value).digest()
    if operator.algorithm is DigestAlgorithm.SHA512:
        digest = hashlib.sha512(raw.value).digest()
    digest_value = BytesValue(value=digest)
    origins = stable_taint_origin_union(
        match.taint_origins,
        operand.value.origins if is_sensitive_value(operand.value) else (),
    )
    digest_evidence = DigestEvidence(algorithm=operator.algorithm, actual=digest_value)
    matched = digest_matches(digest_evidence, expected, operator)
    evidence: DigestEvidence | SensitiveValue[DigestEvidence] = digest_evidence
    if origins:
        evidence = SensitiveValue(digest_evidence, origins=origins)
    return PreparedPredicate(
        kind=PreparedCapabilityKind.DIGEST,
        selector_fingerprint=selector_fingerprint,
        operation_fingerprint=operation_fingerprint(operator),
        implementation=capability_implementation(operator),
        selected_fingerprint=selected,
        operands_fingerprint=operands,
        environment_execution_id=execution_id,
        location=match.location,
        outcome=(
            PreparedPredicateOutcome.SATISFIED if matched else PreparedPredicateOutcome.VIOLATED
        ),
        evidence=evidence,
    )


def _failure(
    operator: DigestOperator,
    match: SelectionMatch,
    execution_id: EnvironmentExecutionId,
    selector_fingerprint: Fingerprint,
    selected: Fingerprint | SensitiveValue[Fingerprint],
    operands: Fingerprint | SensitiveValue[Fingerprint],
    code: DiagnosticCode,
) -> PreparedPredicate:
    return PreparedPredicate(
        kind=PreparedCapabilityKind.DIGEST,
        selector_fingerprint=selector_fingerprint,
        operation_fingerprint=operation_fingerprint(operator),
        implementation=capability_implementation(operator),
        selected_fingerprint=selected,
        operands_fingerprint=operands,
        environment_execution_id=execution_id,
        location=match.location,
        outcome=PreparedPredicateOutcome.ERROR,
        code=code,
    )


__all__ = (
    "DEFAULT_DIGEST_VALIDATION_LIMITS_V1",
    "DigestValidationLimits",
    "prepare_digest_predicate",
)
