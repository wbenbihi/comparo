"""Compatibility and hardening tests for the canonical RunArtifact JSON codec."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.core.artifacts.codec import ArtifactCodecError
from comparo.core.artifacts.codec import ArtifactCodecErrorCode
from comparo.core.artifacts.codec import decode_run_artifact
from comparo.core.artifacts.codec import encode_run_artifact
from comparo.core.artifacts.codec import generate_artifact_schema
from comparo.core.artifacts.codec import render_artifact_schema
from comparo.core.artifacts.values import ArtifactArray
from comparo.core.artifacts.values import ArtifactDecimal
from comparo.core.artifacts.values import ArtifactObject
from comparo.core.artifacts.values import ArtifactObjectMember
from comparo.core.artifacts.values import ArtifactString
from tests.core.artifacts.test_artifact_contracts import _artifact


def test_artifact_json_round_trip_is_canonical_and_deterministic() -> None:
    artifact = _artifact()

    first = encode_run_artifact(artifact, maximum_bytes=100_000)
    second = encode_run_artifact(artifact, maximum_bytes=100_000)

    assert first == second
    assert decode_run_artifact(first, maximum_bytes=100_000) == artifact
    assert msgspec.json.decode(first)["api_version"] == "comparo.artifact/v1alpha1"


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_codec_requires_an_explicit_exact_non_negative_limit(limit: object) -> None:
    with pytest.raises(ArtifactCodecError) as error:
        encode_run_artifact(_artifact(), maximum_bytes=limit)  # type: ignore[arg-type]
    assert error.value.code is ArtifactCodecErrorCode.INVALID_INPUT


def test_codec_error_and_input_types_are_closed() -> None:
    with pytest.raises(TypeError, match="must be typed"):
        ArtifactCodecError(cast("ArtifactCodecErrorCode", "malformed"))
    with pytest.raises(ArtifactCodecError) as invalid_artifact:
        encode_run_artifact(cast("object", {}), maximum_bytes=100)  # type: ignore[arg-type]
    assert invalid_artifact.value.code is ArtifactCodecErrorCode.INVALID_INPUT
    with pytest.raises(ArtifactCodecError) as invalid_payload:
        decode_run_artifact(cast("bytes", bytearray()), maximum_bytes=100)
    assert invalid_payload.value.code is ArtifactCodecErrorCode.INVALID_INPUT


def test_encoding_honors_the_exact_byte_budget() -> None:
    encoded = encode_run_artifact(_artifact(), maximum_bytes=100_000)
    assert encode_run_artifact(_artifact(), maximum_bytes=len(encoded)) == encoded
    with pytest.raises(ArtifactCodecError) as exceeded:
        encode_run_artifact(_artifact(), maximum_bytes=len(encoded) - 1)
    assert exceeded.value.code is ArtifactCodecErrorCode.BUDGET_EXCEEDED


def test_codec_rejects_oversize_malformed_unknown_and_newer_artifacts() -> None:
    encoded = encode_run_artifact(_artifact(), maximum_bytes=100_000)
    with pytest.raises(ArtifactCodecError) as oversize:
        decode_run_artifact(encoded, maximum_bytes=len(encoded) - 1)
    assert oversize.value.code is ArtifactCodecErrorCode.BUDGET_EXCEEDED

    with pytest.raises(ArtifactCodecError) as malformed:
        decode_run_artifact(b'{"api_version":', maximum_bytes=100)
    assert malformed.value.code is ArtifactCodecErrorCode.MALFORMED

    document = msgspec.json.decode(encoded)
    document["unknown"] = True
    with pytest.raises(ArtifactCodecError) as unknown:
        decode_run_artifact(msgspec.json.encode(document), maximum_bytes=100_000)
    assert unknown.value.code is ArtifactCodecErrorCode.MALFORMED

    document.pop("unknown")
    document["api_version"] = "comparo.artifact/v2"
    with pytest.raises(ArtifactCodecError) as unsupported:
        decode_run_artifact(msgspec.json.encode(document), maximum_bytes=100_000)
    assert unsupported.value.code is ArtifactCodecErrorCode.UNSUPPORTED_VERSION


@pytest.mark.parametrize("payload", [b"null", b"[]", b"{}", b'{"api_version":null}'])
def test_codec_rejects_non_artifact_envelopes_without_parser_details(payload: bytes) -> None:
    with pytest.raises(ArtifactCodecError) as error:
        decode_run_artifact(payload, maximum_bytes=100)
    assert error.value.code is ArtifactCodecErrorCode.MALFORMED


def test_tagged_artifact_values_decode_without_union_ambiguity() -> None:
    value = ArtifactObject(
        members=(
            ArtifactObjectMember(
                name="nested",
                value=ArtifactArray(
                    items=(
                        ArtifactString(value="safe"),
                        ArtifactDecimal(value="1.50"),
                    )
                ),
            ),
        )
    )

    assert msgspec.json.decode(msgspec.json.encode(value), type=ArtifactObject) == value


def test_generated_schema_is_stable_and_checked_in() -> None:
    schema = generate_artifact_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert cast("str", schema["$id"]).endswith("comparo-artifact-v1alpha1.schema.json")
    definitions = cast("dict[str, object]", schema["$defs"])
    run_artifact = cast("dict[str, object]", definitions["RunArtifact"])
    properties = cast("dict[str, object]", run_artifact["properties"])
    api_version = cast("dict[str, object]", properties["api_version"])
    assert api_version["const"] == "comparo.artifact/v1alpha1"
    assert schema["x-comparo-validation"] == {
        "scope": "structural",
        "runtimeSemanticValidationRequired": True,
    }
    assert "runtime RunArtifact semantic validation is required" in cast("str", schema["$comment"])
    checked_in = (
        Path(__file__).parents[3]
        / "src"
        / "comparo"
        / "schemas"
        / "comparo-artifact-v1alpha1.schema.json"
    )
    assert checked_in.read_text(encoding="utf-8") == render_artifact_schema()
