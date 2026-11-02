"""Safe inert-YAML decoder behind the run-scoped resolution port."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import cast
from typing import final

import msgspec

from comparo.configuration.yaml_parser import YamlParseLimits
from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.directives import compile_inert_value
from comparo.core.domain.identity import SourceId
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.resolution.models import ProviderError
from comparo.core.resolution.models import ProviderOutcome
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.models import YamlDecodeRequest

_IDENTITY = ValueProviderIdentity(namespace="comparo", name="yaml", version="1")
_SOURCE = SourceId(value="value-yaml")
_ACCESS = SensitiveValueAccess(purpose="yaml-value-decoding")
_LIMIT_CODE = "configuration.yaml.limit-exceeded"


@final
class YamlValueLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite admission limits for one explicit YAML value conversion."""

    maximum_bytes: int = 8 * 1024 * 1024
    maximum_depth: int = 256
    maximum_nodes: int = 1_000_000

    def __post_init__(self) -> None:
        """Require exact positive integers for every decoder budget."""
        for value in (self.maximum_bytes, self.maximum_depth, self.maximum_nodes):
            if type(value) is not int or value < 1:
                raise ValueError("YAML value limits must be positive exact integers")


DEFAULT_YAML_VALUE_LIMITS = YamlValueLimits()


class SafeYamlValueDecoder:
    """Decode exactly one YAML document without constructors or directive interpretation."""

    __slots__ = ("_limits",)

    def __init__(self, limits: YamlValueLimits = DEFAULT_YAML_VALUE_LIMITS) -> None:
        """Bind finite value-conversion budgets."""
        if type(limits) is not YamlValueLimits:
            raise TypeError("safe YAML decoder requires YamlValueLimits")
        self._limits = YamlParseLimits(
            maximum_bytes=limits.maximum_bytes,
            maximum_documents=1,
            maximum_depth=limits.maximum_depth,
            maximum_nodes=limits.maximum_nodes,
        )

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return the stable inert-YAML decoder identity."""
        return _IDENTITY

    async def decode(self, request: YamlDecodeRequest) -> ProviderOutcome:
        """Return one inert JSON value or a value-free conversion failure."""
        if type(request) is not YamlDecodeRequest:
            raise TypeError("safe YAML decoder requires a YamlDecodeRequest")
        if is_sensitive_value(request.document):
            sealed = cast(
                "SensitiveValue[JsonString] | SensitiveValue[BytesValue]",
                request.document,
            )
            document = sealed.reveal(access=_ACCESS)
        else:
            document = cast("JsonString | BytesValue", request.document)
        try:
            text = (
                document.value.decode("utf-8") if type(document) is BytesValue else document.value
            )
        except UnicodeError:
            return ProviderError(code=ResolutionErrorCode.CONVERSION_FAILED)
        try:
            parsed = await asyncio.to_thread(
                partial(parse_yaml_source, source_id=_SOURCE, text=text, limits=self._limits)
            )
        except Exception:  # noqa: BLE001 - provider outcomes never retain parser exception text
            return ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED)
        if parsed.diagnostics:
            code = (
                ResolutionErrorCode.BUDGET_EXCEEDED
                if any(item.code.value == _LIMIT_CODE for item in parsed.diagnostics)
                else ResolutionErrorCode.CONVERSION_FAILED
            )
            return ProviderError(code=code)
        if len(parsed.documents) != 1:
            return ProviderError(code=ResolutionErrorCode.CONVERSION_FAILED)
        return ProviderPresent(value=compile_inert_value(parsed.documents[0].root))


__all__ = (
    "DEFAULT_YAML_VALUE_LIMITS",
    "SafeYamlValueDecoder",
    "YamlValueLimits",
)
