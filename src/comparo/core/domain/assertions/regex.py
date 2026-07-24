"""Pinned compiled regular-expression assertion contracts."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import Fingerprint


class RegexMode(DomainStrEnum):
    """Bounded RE2-compatible matching modes."""

    SEARCH = "search"
    FULL = "full"


class RegexDialect(DomainStrEnum):
    """Versioned bounded regular-expression dialects."""

    RE2 = "re2"


@final
class RegexCompilerIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """The exact compiler whose successful output was fingerprinted."""

    namespace: str
    name: str
    version: str

    def __post_init__(self) -> None:
        """Admit only the initial pinned built-in RE2 compiler contract."""
        if (self.namespace, self.name, self.version) != ("comparo.core", "re2", "1"):
            raise ValueError("regex compiler must be comparo.core/re2@1")


@final
class RegexOperator(msgspec.Struct, frozen=True, kw_only=True):
    """One successfully compiled RE2-compatible regular expression."""

    pattern: str
    mode: RegexMode
    dialect: RegexDialect
    compiler: RegexCompilerIdentity
    compiled_fingerprint: Fingerprint
    negate: bool = False

    def __post_init__(self) -> None:
        """Require source identity, compiled identity, and exact behavior."""
        if type(self.pattern) is not str or not self.pattern:
            raise ValueError("regex pattern must be non-empty text")
        if type(self.mode) is not RegexMode:
            raise TypeError("regex mode must be a RegexMode")
        if type(self.dialect) is not RegexDialect:
            raise TypeError("regex dialect must be a RegexDialect")
        if type(self.compiler) is not RegexCompilerIdentity:
            raise TypeError("regex compiler must be a RegexCompilerIdentity")
        if type(self.compiled_fingerprint) is not Fingerprint:
            raise TypeError("compiled regex fingerprint must be a Fingerprint")
        if type(self.negate) is not bool:
            raise TypeError("regex negate must be a bool")
