"""Authenticated structured artifact body projection tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.adapters.artifacts import InMemorySanitizedBlobStore
from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.application.artifact_references import ArtifactBodyMessage
from comparo.application.artifact_references import ArtifactBodyReference
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.structured_bodies import StructuredBodyFormat
from comparo.application.structured_bodies import StructuredBodyLimits
from comparo.application.structured_bodies import StructuredBodyPage
from comparo.application.structured_bodies import StructuredBodyQuery
from comparo.application.structured_bodies import StructuredJsonKind
from comparo.application.structured_bodies import StructuredJsonPage
from comparo.application.structured_bodies import StructuredSseDataFormat
from comparo.application.structured_bodies import StructuredSsePage
from comparo.application.structured_bodies import read_artifact_structured_body_page
from comparo.core.artifacts import ArtifactBlob
from comparo.core.artifacts import ArtifactCapture
from comparo.core.artifacts import ArtifactExchange
from comparo.core.artifacts import ArtifactResponse
from comparo.core.artifacts import RunArtifact
from comparo.core.artifacts import fingerprint_run_artifact
from tests.adapters.artifacts.test_local_store import _stored_artifact
from tests.application.test_artifacts import _limits as read_limits
from tests.core.artifacts.test_capture_compiler import _limits as storage_limits


def _projection_limits(**changes: int) -> StructuredBodyLimits:
    values = {
        "maximum_document_bytes": 4096,
        "maximum_nodes": 64,
        "maximum_depth": 16,
        "maximum_preview_characters": 12,
        "maximum_pointer_bytes": 256,
        "maximum_sse_events": 16,
        "maximum_sse_line_bytes": 1024,
    }
    values.update(changes)
    return StructuredBodyLimits(**values)


async def _replace_response_body(
    artifact: RunArtifact,
    store: InMemorySanitizedBlobStore,
    payload: bytes,
    media_type: str,
) -> RunArtifact:
    writer = await store.begin()
    await writer.write(payload)
    response_blob = await writer.commit(media_type=media_type)
    first_exchange = artifact.executions[0].attempts[0].exchanges[0]
    assert first_exchange.request is not None
    assert first_exchange.request.body is not None
    request_blob = cast("ArtifactBlob", first_exchange.request.body.blob)

    def replace_response(exchange: ArtifactExchange) -> ArtifactExchange:
        response = cast("ArtifactResponse", exchange.response)
        body = cast("ArtifactCapture", response.body)
        return msgspec.structs.replace(
            exchange,
            response=msgspec.structs.replace(
                response,
                body=msgspec.structs.replace(body, blob=response_blob),
            ),
        )

    executions = tuple(
        msgspec.structs.replace(
            execution,
            attempts=tuple(
                msgspec.structs.replace(
                    attempt,
                    exchanges=tuple(replace_response(exchange) for exchange in attempt.exchanges),
                )
                for attempt in execution.attempts
            ),
        )
        for execution in artifact.executions
    )
    return msgspec.structs.replace(
        artifact,
        executions=executions,
        blobs=tuple(sorted((request_blob, response_blob), key=lambda item: item.sha256)),
    )


def _bundle(
    tmp_path: Path,
    payload: bytes,
    media_type: str,
) -> tuple[Path, ArtifactBodyReference]:
    artifact, store = asyncio.run(_stored_artifact())
    artifact = asyncio.run(_replace_response_body(artifact, store, payload, media_type))
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700, parents=True)
    sink = LocalArtifactBundleSink(root)
    outcome = asyncio.run(sink.persist(artifact, store, storage_limits()))
    sink.close()
    assert outcome.bundle_id == "artifact-0"
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    return root, ArtifactBodyReference(
        artifact_api_version=artifact.api_version,
        artifact_id=artifact.metadata.artifact_id,
        artifact_results_sha256=fingerprint_run_artifact(artifact),
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        exchange_id=exchange.exchange_id,
        message=ArtifactBodyMessage.RESPONSE,
    )


def _read(
    root: Path,
    reference: ArtifactBodyReference,
    query: StructuredBodyQuery,
    *,
    limits: StructuredBodyLimits | None = None,
) -> StructuredBodyPage:
    return asyncio.run(
        read_artifact_structured_body_page(
            root,
            "artifact-0",
            reference,
            read_limits(maximum_blob_bytes=4096),
            limits or _projection_limits(),
            query,
        )
    )


def test_json_body_projection_is_authenticated_pointer_rooted_and_pageable(
    tmp_path: Path,
) -> None:
    root, reference = _bundle(
        tmp_path,
        b'{"user":{"id":7,"roles":["admin","ops"],"note":"abcdef"},"ok":true}',
        "application/problem+json; charset=utf-8",
    )
    query = StructuredBodyQuery(
        format=StructuredBodyFormat.AUTO,
        pointer="/user",
        start=1,
        maximum_items=2,
    )

    page = _read(root, reference, query)

    assert type(page) is StructuredJsonPage
    assert page.source.reference == reference
    assert page.source.total_bytes == 67
    assert page.pointer == "/user"
    assert page.total_nodes == 6
    assert tuple(node.ordinal for node in page.nodes) == (1, 2)
    assert tuple(node.pointer for node in page.nodes) == ("/user/id", "/user/roles")
    assert tuple(node.kind for node in page.nodes) == (
        StructuredJsonKind.INTEGER,
        StructuredJsonKind.ARRAY,
    )
    assert page.next_start == 3
    assert msgspec.json.decode(msgspec.json.encode(page), type=StructuredBodyPage) == page

    final = _read(
        root,
        reference,
        msgspec.structs.replace(query, start=5, maximum_items=3),
    )
    assert type(final) is StructuredJsonPage
    assert final.nodes[0].preview == "abcdef"
    assert final.next_start is None


def test_sse_events_and_nested_json_data_have_independent_pages(tmp_path: Path) -> None:
    payload = (
        b": keepalive\n"
        b"id: stream-1\n"
        b"retry: 1500\n"
        b"event: token\n"
        b'data: {"delta":\n'
        b'data: "hello"}\n\n'
        b"event: done\n"
        b"data: plain text\n\n"
        b"data:\n\n"
        b"event: partial\n"
        b'data: {"ignored":true}'
    )
    root, reference = _bundle(tmp_path, payload, "text/event-stream; charset=utf-8")
    query = StructuredBodyQuery(
        format=StructuredBodyFormat.AUTO,
        start=0,
        maximum_items=2,
    )

    page = _read(root, reference, query)

    assert type(page) is StructuredSsePage
    assert page.total_events == 3
    assert page.next_start == 2
    assert page.trailing_incomplete_event
    first, second = page.events
    assert first.event_type == "token"
    assert first.event_id == "stream-1"
    assert first.retry_milliseconds == 1500
    assert first.data_line_count == 2
    assert first.data_format is StructuredSseDataFormat.JSON
    assert first.json_node_count == 2
    assert first.preview_truncated
    assert second.data_format is StructuredSseDataFormat.TEXT
    assert second.json_node_count is None
    assert msgspec.json.decode(msgspec.json.encode(page), type=StructuredBodyPage) == page

    nested = _read(
        root,
        reference,
        StructuredBodyQuery(
            format=StructuredBodyFormat.SSE,
            pointer="/delta",
            sse_event_index=0,
            start=0,
            maximum_items=5,
        ),
    )
    assert type(nested) is StructuredJsonPage
    assert nested.sse_event_index == 0
    assert nested.nodes[0].preview == "hello"

    empty = _read(root, reference, msgspec.structs.replace(query, start=2))
    assert type(empty) is StructuredSsePage
    assert empty.events[0].data_format is StructuredSseDataFormat.EMPTY


@pytest.mark.parametrize(
    ("payload", "media_type", "query", "changes", "code"),
    [
        (
            b'{"duplicate":1,"duplicate":2}',
            "application/json",
            StructuredBodyQuery(
                format=StructuredBodyFormat.AUTO,
                start=0,
                maximum_items=1,
            ),
            {},
            ArtifactOperationErrorCode.BODY_DECODE_FAILED,
        ),
        (
            b"plain",
            "text/plain",
            StructuredBodyQuery(
                format=StructuredBodyFormat.AUTO,
                start=0,
                maximum_items=1,
            ),
            {},
            ArtifactOperationErrorCode.BODY_FORMAT_UNSUPPORTED,
        ),
        (
            b'{"items":[1,2,3]}',
            "application/json",
            StructuredBodyQuery(
                format=StructuredBodyFormat.JSON,
                start=0,
                maximum_items=1,
            ),
            {"maximum_nodes": 2},
            ArtifactOperationErrorCode.BODY_PROJECTION_LIMIT_EXCEEDED,
        ),
        (
            b'{"items":[1,2,3]}',
            "application/json",
            StructuredBodyQuery(
                format=StructuredBodyFormat.JSON,
                pointer="/missing",
                start=0,
                maximum_items=1,
            ),
            {},
            ArtifactOperationErrorCode.BODY_SUBTREE_NOT_FOUND,
        ),
    ],
)
def test_structured_body_failures_are_closed_and_value_free(
    tmp_path: Path,
    payload: bytes,
    media_type: str,
    query: StructuredBodyQuery,
    changes: dict[str, int],
    code: ArtifactOperationErrorCode,
) -> None:
    root, reference = _bundle(tmp_path, payload, media_type)
    with pytest.raises(ArtifactOperationError) as captured:
        _read(root, reference, query, limits=_projection_limits(**changes))
    assert captured.value.code is code


def test_sse_nested_json_and_event_selection_fail_closed(tmp_path: Path) -> None:
    root, reference = _bundle(tmp_path, b"data: text\n\n", "text/event-stream")
    for event, code in (
        (0, ArtifactOperationErrorCode.BODY_DECODE_FAILED),
        (4, ArtifactOperationErrorCode.BODY_EVENT_NOT_FOUND),
    ):
        with pytest.raises(ArtifactOperationError) as captured:
            _read(
                root,
                reference,
                StructuredBodyQuery(
                    format=StructuredBodyFormat.SSE,
                    sse_event_index=event,
                    start=0,
                    maximum_items=1,
                ),
            )
        assert captured.value.code is code


def test_structured_body_contracts_reject_ambiguous_limits_and_queries() -> None:
    limits = _projection_limits()
    for field in msgspec.structs.fields(StructuredBodyLimits):
        with pytest.raises(ArtifactOperationError) as captured:
            msgspec.structs.replace(limits, **{field.name: 0})
        assert captured.value.code is ArtifactOperationErrorCode.INVALID_LIMIT

    query = StructuredBodyQuery(
        format=StructuredBodyFormat.AUTO,
        start=0,
        maximum_items=1,
    )
    cases: tuple[dict[str, object], ...] = (
        {"format": object()},
        {"start": -1},
        {"start": True},
        {"maximum_items": 0},
        {"maximum_items": True},
        {"pointer": "not-a-pointer"},
        {"pointer": "/bad~2escape"},
        {"sse_event_index": -1},
        {"sse_event_index": True},
        {"format": StructuredBodyFormat.JSON, "sse_event_index": 0},
    )
    for changes in cases:
        with pytest.raises((TypeError, ArtifactOperationError)):
            msgspec.structs.replace(query, **changes)
