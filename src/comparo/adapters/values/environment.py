"""Run-start ambient and declared-dotenv Environment snapshot construction."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import cast
from typing import final

import msgspec

from comparo.adapters.values._filesystem import ConfinedReadCode
from comparo.adapters.values._filesystem import PinnedProjectFiles
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import JsonString
from comparo.core.policy.models import CompiledExecutionPolicy
from comparo.core.resolution.models import EnvironmentSnapshot
from comparo.core.resolution.models import EnvironmentSnapshotEntry
from comparo.core.resolution.provenance import ENV_PROVIDER

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
type DefinitionHandle = DefinitionRef | InlineDefinitionRef


class EnvFileConflict(DomainStrEnum):
    """Conflict behavior between ordered declared dotenv files."""

    ERROR = "error"
    OVERRIDE = "override"
    KEEP = "keep"


class EnvironmentSnapshotErrorCode(DomainStrEnum):
    """Value-free failures produced while preparing one `$env` snapshot."""

    POLICY_MISMATCH = "policy-mismatch"
    SAFETY_UNSUPPORTED = "safety-unsupported"
    CAPABILITY_DENIED = "capability-denied"
    PATH_DENIED = "path-denied"
    NOT_FOUND = "not-found"
    BUDGET_EXCEEDED = "budget-exceeded"
    IO_FAILED = "io-failed"
    INVALID_DOTENV = "invalid-dotenv"
    DUPLICATE_KEY = "duplicate-key"
    CONFLICT = "conflict"


@final
class EnvironmentSourceLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite run-owned limits for Environment source admission."""

    maximum_files: PositiveCount = PositiveCount(value=32)
    maximum_file_bytes: ByteCount = ByteCount(value=1024 * 1024)
    maximum_total_bytes: ByteCount = ByteCount(value=8 * 1024 * 1024)
    maximum_entries: PositiveCount = PositiveCount(value=10_000)
    maximum_line_bytes: ByteCount = ByteCount(value=65_536)

    def __post_init__(self) -> None:
        """Require exact typed budgets."""
        if any(
            type(value) is not PositiveCount for value in (self.maximum_files, self.maximum_entries)
        ):
            raise TypeError("Environment count limits must be PositiveCount")
        if any(
            type(value) is not ByteCount
            for value in (
                self.maximum_file_bytes,
                self.maximum_total_bytes,
                self.maximum_line_bytes,
            )
        ):
            raise TypeError("Environment byte limits must be ByteCount")


DEFAULT_ENVIRONMENT_SOURCE_LIMITS = EnvironmentSourceLimits()


@final
class EnvironmentFileSource(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered Project-relative dotenv declaration."""

    path: JsonString
    configuration: ValueProvenance
    on_conflict: EnvFileConflict = EnvFileConflict.ERROR

    def __post_init__(self) -> None:
        """Require inert path text, safe provenance, and explicit conflict behavior."""
        if type(self.path) is not JsonString:
            raise TypeError("Environment file path must be JsonString")
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("Environment file source requires ValueProvenance")
        if type(self.on_conflict) is not EnvFileConflict:
            raise TypeError("Environment file conflict behavior must be typed")


@final
class EnvironmentInlineValue(msgspec.Struct, frozen=True, kw_only=True):
    """One explicit string value at the strongest Environment source layer."""

    symbol: SymbolPath
    value: JsonString
    configuration: ValueProvenance

    def __post_init__(self) -> None:
        """Require a single portable environment name and immutable text value."""
        if type(self.symbol) is not SymbolPath or len(self.symbol.segments) != 1:
            raise ValueError("Environment inline symbol must be one portable name")
        if _ENVIRONMENT_NAME.fullmatch(self.symbol.segments[0]) is None:
            raise ValueError("Environment inline symbol must use portable variable syntax")
        if type(self.value) is not JsonString:
            raise TypeError("Environment inline value must be JsonString")
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("Environment inline value requires ValueProvenance")


@final
class EnvironmentSnapshotRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Fully lowered sources for one selected Environment at run start."""

    environment: DefinitionRef
    configuration: ValueProvenance
    policy: DefinitionRef
    inherit_process: bool = False
    files: tuple[EnvironmentFileSource, ...] = ()
    values: tuple[EnvironmentInlineValue, ...] = ()
    safety: tuple[DefinitionHandle, ...] = ()

    def __post_init__(self) -> None:
        """Validate one exact Environment, authority revision, and immutable sources."""
        _validate_snapshot_identity(self)
        _validate_snapshot_sources(self)
        _validate_snapshot_safety(self.safety)


@final
class EnvironmentSnapshotBuildResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one prepared snapshot or value-free preparation failure."""

    snapshot: EnvironmentSnapshot | None = None
    error: EnvironmentSnapshotErrorCode | None = None

    def __post_init__(self) -> None:
        """Require exactly one typed success or failure."""
        if self.snapshot is not None and type(self.snapshot) is not EnvironmentSnapshot:
            raise TypeError("Environment snapshot result must contain EnvironmentSnapshot")
        if self.error is not None and type(self.error) is not EnvironmentSnapshotErrorCode:
            raise TypeError("Environment snapshot result error must be typed")
        if (self.snapshot is None) == (self.error is None):
            raise ValueError("Environment snapshot result requires exactly success or failure")


@final
class _DotenvAssignment(msgspec.Struct, frozen=True, kw_only=True):
    """One parsed dotenv assignment retained only inside the live adapter."""

    name: str
    value: str


@final
class _DotenvResult(msgspec.Struct, frozen=True, kw_only=True):
    """Parsed assignments or one value-free dotenv error."""

    assignments: tuple[_DotenvAssignment, ...] = ()
    error: EnvironmentSnapshotErrorCode | None = None

    def __post_init__(self) -> None:
        """Require assignments only on success."""
        if type(self.assignments) is not tuple or any(
            type(item) is not _DotenvAssignment for item in self.assignments
        ):
            raise TypeError("dotenv assignments must be an immutable typed tuple")
        if self.error is not None and type(self.error) is not EnvironmentSnapshotErrorCode:
            raise TypeError("dotenv error must be typed")
        if self.error is not None and self.assignments:
            raise ValueError("failed dotenv result cannot retain assignments")


class RunEnvironmentSnapshotBuilder:
    """Build isolated Environment snapshots from one run-start process capture."""

    __slots__ = ("_ambient", "_files", "_limits", "_policy")

    def __init__(
        self,
        files: PinnedProjectFiles,
        policy: CompiledExecutionPolicy,
        *,
        ambient: Mapping[str, str] | None = None,
        limits: EnvironmentSourceLimits = DEFAULT_ENVIRONMENT_SOURCE_LIMITS,
    ) -> None:
        """Capture authorized ambient values once and bind exact source authority."""
        if type(files) is not PinnedProjectFiles:
            raise TypeError("Environment snapshot builder requires PinnedProjectFiles")
        if type(policy) is not CompiledExecutionPolicy:
            raise TypeError("Environment snapshot builder requires CompiledExecutionPolicy")
        if type(limits) is not EnvironmentSourceLimits:
            raise TypeError("Environment snapshot builder requires EnvironmentSourceLimits")
        source = os.environ if ambient is None else ambient
        if not isinstance(source, Mapping):
            raise TypeError("ambient environment source must be a mapping")
        captured: list[tuple[str, str]] = []
        authority = policy.values.ambient_environment
        allowed = set(authority.names)
        for name, value in source.items():
            if type(name) is not str or type(value) is not str:
                raise TypeError("ambient environment entries must contain exact strings")
            if _ENVIRONMENT_NAME.fullmatch(name) is not None and (
                authority.allow_any or name in allowed
            ):
                captured.append((name, value))
        self._ambient = tuple(sorted(captured))
        self._files = files
        self._policy = policy
        self._limits = limits

    async def build(self, request: EnvironmentSnapshotRequest) -> EnvironmentSnapshotBuildResult:
        """Apply ambient, ordered dotenv, and inline layers without mutating process state."""
        if type(request) is not EnvironmentSnapshotRequest:
            raise TypeError("snapshot builder requires an EnvironmentSnapshotRequest")
        admission = self._admit(request)
        if admission is not None:
            return _failure(admission)
        values: dict[str, tuple[str, ValueProvenance]] = {}
        if request.inherit_process:
            values.update((name, (value, request.configuration)) for name, value in self._ambient)
        file_names: set[str] = set()
        load_error = await self._load_files(request.files, values, file_names)
        if load_error is not None:
            return _failure(load_error)
        for item in request.values:
            values[item.symbol.segments[0]] = (item.value.value, item.configuration)
        if len(values) > self._limits.maximum_entries.value:
            return _failure(EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED)
        entries = tuple(
            EnvironmentSnapshotEntry(
                symbol=SymbolPath(segments=(name,)),
                value=JsonString(value=value),
                configuration=configuration,
            )
            for name, (value, configuration) in sorted(values.items())
        )
        return EnvironmentSnapshotBuildResult(
            snapshot=EnvironmentSnapshot(
                environment=request.environment,
                entries=entries,
                provider=ENV_PROVIDER,
            )
        )

    def _admit(
        self,
        request: EnvironmentSnapshotRequest,
    ) -> EnvironmentSnapshotErrorCode | None:
        if request.policy != self._policy.definition:
            return EnvironmentSnapshotErrorCode.POLICY_MISMATCH
        if request.safety:
            return EnvironmentSnapshotErrorCode.SAFETY_UNSUPPORTED
        if len(request.files) > self._limits.maximum_files.value:
            return EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED
        if request.files and not self._policy.values.allow_project_files:
            return EnvironmentSnapshotErrorCode.CAPABILITY_DENIED
        return None

    async def _load_files(
        self,
        sources: tuple[EnvironmentFileSource, ...],
        values: dict[str, tuple[str, ValueProvenance]],
        file_names: set[str],
    ) -> EnvironmentSnapshotErrorCode | None:
        total_bytes = 0
        for source in sources:
            remaining = self._limits.maximum_total_bytes.value - total_bytes
            maximum = ByteCount(value=min(self._limits.maximum_file_bytes.value, max(0, remaining)))
            read = await self._files.read(source.path.value, maximum_bytes=maximum)
            if read.data is None:
                return _read_error(read.code)
            total_bytes += len(read.data)
            parsed = _parse_dotenv(read.data, self._limits)
            if parsed.error is not None:
                return parsed.error
            merge_error = _merge_file(values, file_names, parsed.assignments, source)
            if merge_error is not None:
                return merge_error
            if len(values) > self._limits.maximum_entries.value:
                return EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED
        return None


def _validate_snapshot_identity(request: EnvironmentSnapshotRequest) -> None:
    if type(request.environment) is not DefinitionRef:
        raise TypeError("Environment snapshot request requires a DefinitionRef")
    if request.environment.identity.kind is not ResourceKind.ENVIRONMENT:
        raise ValueError("Environment snapshot request must identify Environment")
    if type(request.configuration) is not ValueProvenance:
        raise TypeError("Environment snapshot request requires ValueProvenance")
    if type(request.policy) is not DefinitionRef:
        raise TypeError("Environment snapshot request policy must be a DefinitionRef")
    if request.policy.identity.kind is not ResourceKind.EXECUTION_POLICY:
        raise ValueError("Environment snapshot request must identify ExecutionPolicy")
    if type(request.inherit_process) is not bool:
        raise TypeError("Environment inherit-process flag must be boolean")


def _validate_snapshot_sources(request: EnvironmentSnapshotRequest) -> None:
    if type(request.files) is not tuple or any(
        type(item) is not EnvironmentFileSource for item in request.files
    ):
        raise TypeError("Environment files must be an immutable typed tuple")
    if type(request.values) is not tuple or any(
        type(item) is not EnvironmentInlineValue for item in request.values
    ):
        raise TypeError("Environment values must be an immutable typed tuple")
    symbols = tuple(item.symbol for item in request.values)
    if len(symbols) != len(set(symbols)):
        raise ValueError("Environment inline symbols must be unique")


def _validate_snapshot_safety(safety: tuple[DefinitionHandle, ...]) -> None:
    if type(safety) is not tuple or any(
        type(item) not in {DefinitionRef, InlineDefinitionRef} for item in safety
    ):
        raise TypeError("Environment safety handles must be an immutable typed tuple")
    for item in safety:
        kind = item.identity.kind if type(item) is DefinitionRef else item.kind
        if kind is not ResourceKind.SAFETY_PROFILE:
            raise ValueError("Environment safety handles must identify SafetyProfile")


def _parse_dotenv(data: bytes, limits: EnvironmentSourceLimits) -> _DotenvResult:
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return _DotenvResult(error=EnvironmentSnapshotErrorCode.INVALID_DOTENV)
    if "\x00" in text:
        return _DotenvResult(error=EnvironmentSnapshotErrorCode.INVALID_DOTENV)
    assignments: list[_DotenvAssignment] = []
    names: set[str] = set()
    for line in text.splitlines():
        if len(line.encode("utf-8")) > limits.maximum_line_bytes.value:
            return _DotenvResult(error=EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*)", line)
        if match is None:
            return _DotenvResult(error=EnvironmentSnapshotErrorCode.INVALID_DOTENV)
        name = match.group(1)
        if name in names:
            return _DotenvResult(error=EnvironmentSnapshotErrorCode.DUPLICATE_KEY)
        value = _dotenv_value(match.group(2))
        if value is None:
            return _DotenvResult(error=EnvironmentSnapshotErrorCode.INVALID_DOTENV)
        names.add(name)
        assignments.append(_DotenvAssignment(name=name, value=value))
        if len(assignments) > limits.maximum_entries.value:
            return _DotenvResult(error=EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED)
    return _DotenvResult(assignments=tuple(assignments))


def _dotenv_value(raw: str) -> str | None:
    value = raw.rstrip(" \t")
    if not value:
        return ""
    if value[0] == "'":
        return value[1:-1] if len(value) >= 2 and value[-1] == "'" else None
    if value[0] != '"':
        return value
    if len(value) < 2 or value[-1] != '"':
        return None
    decoded: list[str] = []
    escaped = False
    escapes = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
    for character in value[1:-1]:
        if escaped:
            replacement = escapes.get(character)
            if replacement is None:
                return None
            decoded.append(replacement)
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            decoded.append(character)
    return None if escaped else "".join(decoded)


def _merge_file(
    values: dict[str, tuple[str, ValueProvenance]],
    file_names: set[str],
    assignments: tuple[_DotenvAssignment, ...],
    source: EnvironmentFileSource,
) -> EnvironmentSnapshotErrorCode | None:
    for assignment in assignments:
        if assignment.name in file_names:
            if source.on_conflict is EnvFileConflict.ERROR:
                return EnvironmentSnapshotErrorCode.CONFLICT
            if source.on_conflict is EnvFileConflict.KEEP:
                continue
        values[assignment.name] = (assignment.value, source.configuration)
        file_names.add(assignment.name)
    return None


def _read_error(code: ConfinedReadCode | None) -> EnvironmentSnapshotErrorCode:
    mapping = {
        ConfinedReadCode.NOT_FOUND: EnvironmentSnapshotErrorCode.NOT_FOUND,
        ConfinedReadCode.PATH_DENIED: EnvironmentSnapshotErrorCode.PATH_DENIED,
        ConfinedReadCode.BUDGET_EXCEEDED: EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED,
        ConfinedReadCode.IO_FAILED: EnvironmentSnapshotErrorCode.IO_FAILED,
        ConfinedReadCode.CLOSED: EnvironmentSnapshotErrorCode.IO_FAILED,
    }
    return mapping[cast("ConfinedReadCode", code)]


def _failure(code: EnvironmentSnapshotErrorCode) -> EnvironmentSnapshotBuildResult:
    return EnvironmentSnapshotBuildResult(error=code)


__all__ = (
    "DEFAULT_ENVIRONMENT_SOURCE_LIMITS",
    "EnvFileConflict",
    "EnvironmentFileSource",
    "EnvironmentInlineValue",
    "EnvironmentSnapshotBuildResult",
    "EnvironmentSnapshotErrorCode",
    "EnvironmentSnapshotRequest",
    "EnvironmentSourceLimits",
    "RunEnvironmentSnapshotBuilder",
)
