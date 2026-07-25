"""Canonical bounded JSON codec for the independently versioned RunArtifact API."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import msgspec

from comparo.core.artifacts.common import ARTIFACT_API_VERSION
from comparo.core.artifacts.model import RunArtifact
from comparo.core.domain.enums import DomainStrEnum

_ENCODER = msgspec.json.Encoder(order="deterministic")
_DECODER = msgspec.json.Decoder(RunArtifact)


class ArtifactCodecErrorCode(DomainStrEnum):
    """Closed value-free artifact codec failure categories."""

    INVALID_INPUT = "invalid-input"
    BUDGET_EXCEEDED = "budget-exceeded"
    MALFORMED = "malformed"
    UNSUPPORTED_VERSION = "unsupported-version"


class ArtifactCodecError(ValueError):
    """A bounded artifact codec failure that never embeds source content."""

    __slots__ = ("code",)

    def __init__(self, code: ArtifactCodecErrorCode) -> None:
        """Retain only a closed safe failure code."""
        if type(code) is not ArtifactCodecErrorCode:
            raise TypeError("artifact codec error code must be typed")
        self.code = code
        super().__init__(code.value)


def encode_run_artifact(artifact: RunArtifact, *, maximum_bytes: int) -> bytes:
    """Encode one exact artifact canonically within a caller-owned finite limit."""
    _require_limit(maximum_bytes)
    if type(artifact) is not RunArtifact:
        raise ArtifactCodecError(ArtifactCodecErrorCode.INVALID_INPUT)
    payload = _ENCODER.encode(artifact)
    if len(payload) > maximum_bytes:
        raise ArtifactCodecError(ArtifactCodecErrorCode.BUDGET_EXCEEDED)
    return payload


def decode_run_artifact(payload: bytes, *, maximum_bytes: int) -> RunArtifact:
    """Decode only the supported artifact API without returning parser diagnostics."""
    _require_limit(maximum_bytes)
    if type(payload) is not bytes:
        raise ArtifactCodecError(ArtifactCodecErrorCode.INVALID_INPUT)
    if len(payload) > maximum_bytes:
        raise ArtifactCodecError(ArtifactCodecErrorCode.BUDGET_EXCEEDED)
    try:
        envelope = msgspec.json.decode(payload)
    except msgspec.DecodeError as error:
        raise ArtifactCodecError(ArtifactCodecErrorCode.MALFORMED) from error
    if type(envelope) is not dict:
        raise ArtifactCodecError(ArtifactCodecErrorCode.MALFORMED)
    version = cast("Mapping[object, object]", envelope).get("api_version")
    if type(version) is not str:
        raise ArtifactCodecError(ArtifactCodecErrorCode.MALFORMED)
    if version != ARTIFACT_API_VERSION:
        raise ArtifactCodecError(ArtifactCodecErrorCode.UNSUPPORTED_VERSION)
    try:
        return _DECODER.decode(payload)
    except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError) as error:
        raise ArtifactCodecError(ArtifactCodecErrorCode.MALFORMED) from error


def generate_artifact_schema() -> dict[str, object]:
    """Generate the structural schema; runtime validation enforces semantic invariants."""
    generated = msgspec.json.schema(RunArtifact)
    definitions = cast("dict[str, object]", generated["$defs"])
    run_artifact = cast("dict[str, object]", definitions["RunArtifact"])
    properties = cast("dict[str, object]", run_artifact["properties"])
    api_version = cast("dict[str, object]", properties["api_version"])
    api_version["const"] = ARTIFACT_API_VERSION
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://comparo.dev/schemas/comparo-artifact-v1alpha1.schema.json",
        "$comment": (
            "This schema validates structural shape only; runtime RunArtifact semantic "
            "validation is required."
        ),
        "x-comparo-validation": {
            "scope": "structural",
            "runtimeSemanticValidationRequired": True,
        },
        **generated,
    }


def render_artifact_schema() -> str:
    """Render canonical checked-in artifact schema text with a stable trailing newline."""
    return json.dumps(generate_artifact_schema(), indent=2, sort_keys=True) + "\n"


def _require_limit(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ArtifactCodecError(ArtifactCodecErrorCode.INVALID_INPUT)
