"""ExecutionPolicy-bound `$file` adapter for local Project files."""

from __future__ import annotations

from typing import cast

from comparo.adapters.values._filesystem import ConfinedReadCode
from comparo.adapters.values._filesystem import PinnedProjectFiles
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import UnavailableReason
from comparo.core.policy.models import CompiledExecutionPolicy
from comparo.core.resolution.conversions import trim_bytes
from comparo.core.resolution.models import FileReadRequest
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderError
from comparo.core.resolution.models import ProviderOutcome
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.models import ProviderUnavailable

_IDENTITY = ValueProviderIdentity(namespace="comparo", name="project-file", version="1")
_ACCESS = SensitiveValueAccess(purpose="project-file-read")


class ProjectFileValueProvider:
    """Authorize and confine one `$file` read to the pinned Project root."""

    __slots__ = ("_files", "_policy")

    def __init__(self, files: PinnedProjectFiles, policy: CompiledExecutionPolicy) -> None:
        """Bind filesystem capability to one exact compiled ExecutionPolicy revision."""
        if type(files) is not PinnedProjectFiles:
            raise TypeError("project file provider requires PinnedProjectFiles")
        if type(policy) is not CompiledExecutionPolicy:
            raise TypeError("project file provider requires CompiledExecutionPolicy")
        self._files = files
        self._policy = policy

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return the stable built-in Project-file provider identity."""
        return _IDENTITY

    async def read(self, request: FileReadRequest) -> ProviderOutcome:
        """Apply exact authority, containment, byte limits, and explicit trimming."""
        if type(request) is not FileReadRequest:
            raise TypeError("project file provider requires a FileReadRequest")
        if request.policy != self._policy.definition:
            return ProviderBlocked(code=ResolutionBlockCode.SOURCE_DENIED)
        if request.safety:
            return ProviderBlocked(code=ResolutionBlockCode.SOURCE_DENIED)
        if not self._policy.values.allow_project_files:
            return ProviderBlocked(code=ResolutionBlockCode.CAPABILITY_DENIED)
        if is_sensitive_value(request.path):
            path = cast("SensitiveValue[JsonString]", request.path).reveal(access=_ACCESS).value
        else:
            path = cast("JsonString", request.path).value
        result = await self._files.read(path, maximum_bytes=request.maximum_bytes)
        if result.data is not None:
            value = trim_bytes(BytesValue(value=result.data), request.trim)
            return ProviderPresent(value=value)
        if result.code is ConfinedReadCode.NOT_FOUND:
            return ProviderUnavailable(reason=UnavailableReason.NOT_FOUND)
        if result.code is ConfinedReadCode.PATH_DENIED:
            return ProviderBlocked(code=ResolutionBlockCode.PATH_DENIED)
        if result.code is ConfinedReadCode.BUDGET_EXCEEDED:
            return ProviderError(code=ResolutionErrorCode.BUDGET_EXCEEDED)
        return ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED)


__all__ = ("ProjectFileValueProvider",)
