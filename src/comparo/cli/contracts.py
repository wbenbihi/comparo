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
    BINARY = "binary"


class CliOutputMode(StrEnum):
    """User-selected presentation mode for structured command results."""

    HUMAN = "human"
    JSON = "json"


class CliColorMode(StrEnum):
    """Explicit terminal-color preference applied only by the outer adapter."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class CliReportFormat(StrEnum):
    """Closed report projections supported by the CLI."""

    JSON = "json"
    JUNIT = "junit"
    SARIF = "sarif"
    MARKDOWN = "markdown"


@final
class CliArtifactLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Explicit finite artifact read limits carried by every CLI operation."""

    maximum_blobs: int
    maximum_blob_bytes: int
    maximum_total_blob_bytes: int
    maximum_manifest_bytes: int
    maximum_report_bytes: int
    maximum_redaction_patterns: int
    maximum_redaction_pattern_bytes: int
    maximum_redaction_value_bytes: int
    maximum_redaction_value_nodes: int

    def __post_init__(self) -> None:
        """Require exact integers while leaving range validation to the application service."""
        if any(
            type(value) is not int
            for value in (
                self.maximum_blobs,
                self.maximum_blob_bytes,
                self.maximum_total_blob_bytes,
                self.maximum_manifest_bytes,
                self.maximum_report_bytes,
                self.maximum_redaction_patterns,
                self.maximum_redaction_pattern_bytes,
                self.maximum_redaction_value_bytes,
                self.maximum_redaction_value_nodes,
            )
        ):
            raise TypeError("CLI artifact limits must be exact integers")


DEFAULT_CLI_ARTIFACT_ROOT_V1 = Path(".comparo/artifacts")
DEFAULT_CLI_ARTIFACT_LIMITS_V1 = CliArtifactLimits(
    maximum_blobs=4096,
    maximum_blob_bytes=64 * 1024 * 1024,
    maximum_total_blob_bytes=512 * 1024 * 1024,
    maximum_manifest_bytes=64 * 1024 * 1024,
    maximum_report_bytes=64 * 1024 * 1024,
    maximum_redaction_patterns=4096,
    maximum_redaction_pattern_bytes=1024 * 1024,
    maximum_redaction_value_bytes=256 * 1024,
    maximum_redaction_value_nodes=4096,
)
DEFAULT_CLI_BLOB_OUTPUT_BYTES_V1 = 64 * 1024 * 1024


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
        empty_document = not self.payload and self.payload_kind is not CliPayloadKind.BINARY
        if (human and (self.payload or not self.messages)) or (
            not human and (self.messages or empty_document)
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
class ArtifactReadRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Framework-neutral input shared by artifact inspection and verification."""

    root: Path
    bundle_id: str
    limits: CliArtifactLimits
    output: CliOutputMode

    def __post_init__(self) -> None:
        """Require exact adapter coordinates and closed presentation contracts."""
        if not isinstance(self.root, Path):
            raise TypeError("artifact root must be a Path")
        if type(self.bundle_id) is not str:
            raise TypeError("artifact bundle id must be text")
        if type(self.limits) is not CliArtifactLimits:
            raise TypeError("artifact read limits must be CliArtifactLimits")
        if type(self.output) is not CliOutputMode:
            raise TypeError("artifact output mode must be typed")


@final
class ArtifactBlobRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Framework-neutral input for one bounded raw sanitized-blob read."""

    root: Path
    bundle_id: str
    sha256: str
    limits: CliArtifactLimits
    maximum_output_bytes: int

    def __post_init__(self) -> None:
        """Require exact adapter coordinates while deferring range validation."""
        if not isinstance(self.root, Path):
            raise TypeError("artifact blob root must be a Path")
        if type(self.bundle_id) is not str or type(self.sha256) is not str:
            raise TypeError("artifact blob coordinates must be text")
        if type(self.limits) is not CliArtifactLimits:
            raise TypeError("artifact blob limits must be CliArtifactLimits")
        if type(self.maximum_output_bytes) is not int:
            raise TypeError("artifact blob output limit must be an exact integer")


@final
class ReportRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Framework-neutral input for one bounded report rendered to stdout."""

    root: Path
    bundle_id: str
    limits: CliArtifactLimits
    format: CliReportFormat

    def __post_init__(self) -> None:
        """Require exact local coordinates and one closed report format."""
        if not isinstance(self.root, Path):
            raise TypeError("report artifact root must be a Path")
        if type(self.bundle_id) is not str:
            raise TypeError("report bundle id must be text")
        if type(self.limits) is not CliArtifactLimits:
            raise TypeError("report limits must be CliArtifactLimits")
        if type(self.format) is not CliReportFormat:
            raise TypeError("report format must be typed")


def _validate_plan_paths(
    config: Path,
    sources: tuple[Path, ...],
    policy: Path | None,
) -> None:
    if not isinstance(config, Path):
        raise TypeError("plan config must be a Path")
    if type(sources) is not tuple or any(not isinstance(source, Path) for source in sources):
        raise TypeError("plan additional sources must be an immutable Path tuple")
    if policy is not None and not isinstance(policy, Path):
        raise TypeError("plan policy must be a Path or None")


def _validate_plan_text_options(
    execution_profile: str | None,
    baseline: str | None,
    collections: tuple[tuple[str, ...], ...],
) -> None:
    if execution_profile is not None and type(execution_profile) is not str:
        raise TypeError("plan execution profile must be text or None")
    for values in collections:
        if type(values) is not tuple or any(type(item) is not str for item in values):
            raise TypeError("plan repeated options must be immutable text tuples")
    if baseline is not None and type(baseline) is not str:
        raise TypeError("plan baseline must be text or None")


@final
class PlanRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Framework-neutral CLI inputs for one offline plan preview."""

    config: Path
    additional_sources: tuple[Path, ...]
    policy: Path | None
    execution_profile: str | None
    subjects: tuple[str, ...]
    environments: tuple[str, ...]
    baseline: str | None
    candidates: tuple[str, ...]
    matrices: tuple[str, ...]
    trials: int | None
    maximum_attempts: int | None
    fail_fast: bool | None
    budgets: tuple[str, ...]
    output: CliOutputMode

    def __post_init__(self) -> None:
        """Reject adapter type drift without interpreting authored values."""
        _validate_plan_paths(self.config, self.additional_sources, self.policy)
        _validate_plan_text_options(
            self.execution_profile,
            self.baseline,
            (
                self.subjects,
                self.environments,
                self.candidates,
                self.matrices,
                self.budgets,
            ),
        )
        for value in (self.trials, self.maximum_attempts):
            if value is not None and type(value) is not int:
                raise TypeError("plan numeric options must be exact integers or None")
        if self.fail_fast is not None and type(self.fail_fast) is not bool:
            raise TypeError("plan fail-fast must be bool or None")
        if type(self.output) is not CliOutputMode:
            raise TypeError("plan output mode must be typed")


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
