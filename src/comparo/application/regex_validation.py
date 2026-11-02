"""Pinned RE2 compilation and predicate preparation outside the pure kernel."""

from __future__ import annotations

import hashlib
from typing import Protocol
from typing import cast
from typing import final

import msgspec
import re2  # type: ignore[import-untyped]

from comparo.core.assertions.bindings import capability_implementation
from comparo.core.assertions.bindings import operands_fingerprint
from comparo.core.assertions.bindings import operation_fingerprint
from comparo.core.assertions.bindings import selected_fingerprint
from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedPredicate
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.domain.assertions.regex import RegexCompilerIdentity
from comparo.core.domain.assertions.regex import RegexDialect
from comparo.core.domain.assertions.regex import RegexMode
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonString

_ACCESS = SensitiveValueAccess(purpose="regex.validation.prepare")
_COMPILER = RegexCompilerIdentity(namespace="comparo.core", name="re2", version="1")
_INPUT_INVALID = DiagnosticCode(value="regex.input.invalid")
_INPUT_LIMIT = DiagnosticCode(value="regex.input.limit-exceeded")


class _RegexProgram(Protocol):
    """Minimal process-local RE2 program surface."""

    def search(self, text: str) -> object | None:
        """Search one text value."""

    def fullmatch(self, text: str) -> object | None:
        """Match one complete text value."""


@final
class RegexValidationLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned limits around linear-time regex compilation and matching."""

    maximum_pattern_bytes: int
    maximum_input_bytes: int

    def __post_init__(self) -> None:
        """Require explicit positive exact integer ceilings."""
        if (
            type(self.maximum_pattern_bytes) is not int
            or self.maximum_pattern_bytes < 1
            or type(self.maximum_input_bytes) is not int
            or self.maximum_input_bytes < 1
        ):
            raise ValueError("regex validation limits must be positive exact integers")


DEFAULT_REGEX_VALIDATION_LIMITS_V1 = RegexValidationLimits(
    maximum_pattern_bytes=64 * 1024,
    maximum_input_bytes=16 * 1024 * 1024,
)


@final
class PreparedRegex(msgspec.Struct, frozen=True, kw_only=True):
    """One exact regex operator and its process-local RE2 program."""

    operator: RegexOperator
    program: _RegexProgram

    def __post_init__(self) -> None:
        """Require exact immutable public identity and a private compiled program."""
        if type(self.operator) is not RegexOperator or self.program is None:
            raise TypeError("prepared regex requires an operator and compiled program")


class PreparedRegexCatalog:
    """Exact lookup for regex programs prepared for one admitted run."""

    __slots__ = ("_entries", "_limits")

    def __init__(
        self,
        entries: tuple[PreparedRegex, ...],
        limits: RegexValidationLimits,
    ) -> None:
        """Retain unique operators and caller-owned input bounds."""
        if type(entries) is not tuple or any(type(item) is not PreparedRegex for item in entries):
            raise TypeError("prepared regex entries must be an immutable typed tuple")
        if len({item.operator for item in entries}) != len(entries):
            raise ValueError("prepared regex operators must be unique")
        if type(limits) is not RegexValidationLimits:
            raise TypeError("prepared regex catalog requires typed limits")
        self._entries = entries
        self._limits = limits

    @property
    def entries(self) -> tuple[PreparedRegex, ...]:
        """Return exact prepared entries for inspection and testing."""
        return self._entries

    def predicate(
        self,
        operator: RegexOperator,
        match: SelectionMatch,
        execution_id: EnvironmentExecutionId,
        selector_fingerprint: Fingerprint,
    ) -> PreparedPredicate:
        """Match one selected text using only its exact precompiled RE2 program."""
        entry = next((item for item in self._entries if item.operator == operator), None)
        selected = selected_fingerprint(match)
        operands = operands_fingerprint(())
        if entry is None:
            return _predicate(
                operator,
                match,
                execution_id,
                selector_fingerprint,
                selected,
                operands,
                PreparedPredicateOutcome.ERROR,
                DiagnosticCode(value="regex.program.missing"),
            )
        value = (
            match.value.reveal(access=_ACCESS) if is_sensitive_value(match.value) else match.value
        )
        if type(value) not in {JsonString, HttpFieldTextValue}:
            return _predicate(
                operator,
                match,
                execution_id,
                selector_fingerprint,
                selected,
                operands,
                PreparedPredicateOutcome.ERROR,
                _INPUT_INVALID,
            )
        text = cast("JsonString | HttpFieldTextValue", value).value
        if len(text.encode("utf-8")) > self._limits.maximum_input_bytes:
            return _predicate(
                operator,
                match,
                execution_id,
                selector_fingerprint,
                selected,
                operands,
                PreparedPredicateOutcome.ERROR,
                _INPUT_LIMIT,
            )
        matched = (
            entry.program.fullmatch(text) is not None
            if operator.mode is RegexMode.FULL
            else entry.program.search(text) is not None
        )
        return _predicate(
            operator,
            match,
            execution_id,
            selector_fingerprint,
            selected,
            operands,
            (PreparedPredicateOutcome.SATISFIED if matched else PreparedPredicateOutcome.VIOLATED),
            None,
        )


def compile_regex(
    pattern: str,
    *,
    mode: RegexMode,
    negate: bool,
    limits: RegexValidationLimits = DEFAULT_REGEX_VALIDATION_LIMITS_V1,
) -> PreparedRegex:
    """Compile one bounded RE2 pattern and bind its exact implementation identity."""
    if type(pattern) is not str or type(mode) is not RegexMode or type(negate) is not bool:
        raise TypeError("regex compilation requires static typed arguments")
    if len(pattern.encode("utf-8")) > limits.maximum_pattern_bytes:
        raise ValueError("regex pattern exceeds the configured bound")
    invalid = False
    try:
        program = re2.compile(pattern)
    except re2.error:
        invalid = True
    else:
        fingerprint = _fingerprint(pattern, mode.value)
        return PreparedRegex(
            operator=RegexOperator(
                pattern=pattern,
                mode=mode,
                dialect=RegexDialect.RE2,
                compiler=_COMPILER,
                compiled_fingerprint=fingerprint,
                negate=negate,
            ),
            program=program,
        )
    if invalid:
        raise ValueError("regex pattern is invalid for RE2")
    raise ValueError("regex pattern is invalid for RE2")  # pragma: no cover


def prepare_compiled_regex(
    operator: RegexOperator,
    limits: RegexValidationLimits,
) -> PreparedRegex:
    """Recompile and verify a previously lowered exact regex identity."""
    prepared = compile_regex(
        operator.pattern,
        mode=operator.mode,
        negate=operator.negate,
        limits=limits,
    )
    if prepared.operator != operator:
        raise ValueError("compiled regex identity is not reproducible")
    return prepared


def _predicate(
    operator: RegexOperator,
    match: SelectionMatch,
    execution_id: EnvironmentExecutionId,
    selector_fingerprint: Fingerprint,
    selected: Fingerprint | SensitiveValue[Fingerprint],
    operands: Fingerprint | SensitiveValue[Fingerprint],
    outcome: PreparedPredicateOutcome,
    code: DiagnosticCode | None,
) -> PreparedPredicate:
    return PreparedPredicate(
        kind=PreparedCapabilityKind.REGEX,
        selector_fingerprint=selector_fingerprint,
        operation_fingerprint=operation_fingerprint(operator),
        implementation=capability_implementation(operator),
        selected_fingerprint=selected,
        operands_fingerprint=operands,
        environment_execution_id=execution_id,
        location=match.location,
        outcome=outcome,
        code=code,
    )


def _fingerprint(pattern: str, mode: str) -> Fingerprint:
    payload = f"comparo.re2.v1\x00{mode}\x00{pattern}".encode()
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(payload).hexdigest()),
    )


__all__ = (
    "DEFAULT_REGEX_VALIDATION_LIMITS_V1",
    "PreparedRegex",
    "PreparedRegexCatalog",
    "RegexValidationLimits",
    "compile_regex",
    "prepare_compiled_regex",
)
