"""Framework-neutral contracts for the Comparo command-line adapter."""

from __future__ import annotations

from enum import IntEnum
from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec


class CliExitCode(IntEnum):
    """Stable process outcomes shared by every Comparo command."""

    SUCCESS = 0
    GATE_FAILED = 1
    INVALID_INVOCATION = 2
    EXECUTION_INCOMPLETE = 3
    INTERNAL_ERROR = 4
    INTERRUPTED = 130


class CliMessageLevel(StrEnum):
    """Closed presentation roles understood by the terminal renderer."""

    SUCCESS = "success"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CliPayloadKind(StrEnum):
    """How a successful command payload must cross the terminal boundary."""

    HUMAN = "human"
    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"
    RST = "rst"


class CliOutputMode(StrEnum):
    """User-selected presentation mode for structured command results."""

    HUMAN = "human"
    JSON = "json"


class CliColorMode(StrEnum):
    """Explicit terminal-color preference applied only by the outer adapter."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


@final
class CliMessage(msgspec.Struct, frozen=True, kw_only=True):
    """One already-safe human message without terminal-framework markup."""

    level: CliMessageLevel
    text: str

    def __post_init__(self) -> None:
        """Require typed roles and bounded non-empty safe text."""
        if type(self.level) is not CliMessageLevel:
            raise TypeError("CLI message level must be typed")
        if type(self.text) is not str or not self.text or len(self.text) > 4096:
            raise ValueError("CLI message text must be bounded non-empty text")


@final
class CliCommandResult(msgspec.Struct, frozen=True, kw_only=True):
    """A complete command result rendered only by the outer CLI adapter."""

    exit_code: CliExitCode
    payload_kind: CliPayloadKind
    messages: tuple[CliMessage, ...] = ()
    payload: bytes = b""

    def __post_init__(self) -> None:
        """Keep human messages and byte payloads as disjoint output modes."""
        if type(self.exit_code) is not CliExitCode:
            raise TypeError("CLI result exit code must be typed")
        if type(self.payload_kind) is not CliPayloadKind:
            raise TypeError("CLI result payload kind must be typed")
        if type(self.messages) is not tuple or any(
            type(message) is not CliMessage for message in self.messages
        ):
            raise TypeError("CLI result messages must be an immutable typed tuple")
        if type(self.payload) is not bytes:
            raise TypeError("CLI result payload must be immutable bytes")
        human = self.payload_kind is CliPayloadKind.HUMAN
        if (human and (self.payload or not self.messages)) or (
            not human and (self.messages or not self.payload)
        ):
            raise ValueError("CLI result must contain exactly its selected output representation")


@final
class ValidateRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Framework-neutral input for configuration loading and compilation."""

    config: Path
    additional_sources: tuple[Path, ...]
    output: CliOutputMode

    def __post_init__(self) -> None:
        """Require exact local paths and a closed output mode."""
        if not isinstance(self.config, Path):
            raise TypeError("validation config must be a Path")
        if type(self.additional_sources) is not tuple or any(
            not isinstance(source, Path) for source in self.additional_sources
        ):
            raise TypeError("validation additional sources must be an immutable Path tuple")
        if type(self.output) is not CliOutputMode:
            raise TypeError("validation output mode must be typed")


@final
class DoctorRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Framework-neutral input for offline security self-checks."""

    output: CliOutputMode

    def __post_init__(self) -> None:
        """Require a closed output mode."""
        if type(self.output) is not CliOutputMode:
            raise TypeError("doctor output mode must be typed")


@final
class CliInvocation(msgspec.Struct, frozen=True, kw_only=True):
    """One parsed callback result awaiting outer terminal rendering."""

    result: CliCommandResult
    color: CliColorMode

    def __post_init__(self) -> None:
        """Keep framework parsing separate from rendering and exit handling."""
        if type(self.result) is not CliCommandResult:
            raise TypeError("CLI invocation result must be typed")
        if type(self.color) is not CliColorMode:
            raise TypeError("CLI invocation color mode must be typed")
