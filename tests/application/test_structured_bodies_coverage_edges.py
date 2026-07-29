"""Contract and hostile-input edges for structured body projections."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application import structured_bodies as bodies
from comparo.application.artifact_references import ArtifactBodyMessage
from comparo.application.artifact_references import ArtifactBodyReference
from comparo.application.artifacts import ArtifactBodyWindow
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.structured_bodies import StructuredBodyFormat
from comparo.application.structured_bodies import StructuredBodyLimits
from comparo.application.structured_bodies import StructuredBodyQuery
from comparo.application.structured_bodies import StructuredJsonKind
from comparo.application.structured_bodies import StructuredJsonPage
from comparo.application.structured_bodies import StructuredSseDataFormat
from comparo.application.structured_bodies import StructuredSsePage
from comparo.application.structured_bodies import read_artifact_structured_body_page
from tests.application.test_artifacts import _limits as read_limits
from tests.application.test_structured_bodies import _bundle
from tests.application.test_structured_bodies import _projection_limits
from tests.application.test_structured_bodies import _read


def _reference() -> ArtifactBodyReference:
    return ArtifactBodyReference(
        artifact_api_version="comparo.artifact/v1alpha1",
        artifact_id="artifact-0",
        artifact_results_sha256="0" * 64,
        execution_id="execution-0",
        attempt_id="attempt-0",
        exchange_id="exchange-0",
        message=ArtifactBodyMessage.RESPONSE,
    )


def _window(**changes: object) -> ArtifactBodyWindow:
    value = ArtifactBodyWindow(
        reference=_reference(),
        source_state="complete",
        extent="complete",
        reason=None,
        media_type="application/json",
        total_bytes=2,
        offset=0,
        content=b"{}",
        next_offset=None,
        redaction_count=0,
    )
    return msgspec.structs.replace(value, **changes)


def _pages(tmp_path: Path) -> tuple[StructuredJsonPage, StructuredSsePage]:
    json_root, json_reference = _bundle(tmp_path / "json", b'{"a":1}', "application/json")
    json_page = _read(
        json_root,
        json_reference,
        StructuredBodyQuery(
            format=StructuredBodyFormat.AUTO,
            start=0,
            maximum_items=2,
        ),
    )
    sse_root, sse_reference = _bundle(
        tmp_path / "sse",
        b'data: {"ok":true}\n\ndata:\n\n',
        "text/event-stream",
    )
    sse_page = _read(
        sse_root,
        sse_reference,
        StructuredBodyQuery(
            format=StructuredBodyFormat.AUTO,
            start=0,
            maximum_items=2,
        ),
    )
    return cast("StructuredJsonPage", json_page), cast("StructuredSsePage", sse_page)


def test_source_node_event_and_page_contracts_reject_invalid_state(tmp_path: Path) -> None:
    json_page, sse_page = _pages(tmp_path)
    assert json_page.format is StructuredBodyFormat.JSON
    assert sse_page.format is StructuredBodyFormat.SSE

    source_cases: tuple[dict[str, object], ...] = (
        {"reference": object()},
        {"media_type": object()},
        {"source_state": ""},
        {"extent": ""},
        {"reason": ""},
        {"reason": object()},
        {"total_bytes": -1},
        {"total_bytes": True},
        {"redaction_count": -1},
        {"redaction_count": True},
    )
    for changes in source_cases:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(json_page.source, **changes)

    node = json_page.nodes[0]
    node_cases: tuple[dict[str, object], ...] = (
        {"ordinal": -1},
        {"ordinal": True},
        {"depth": -1},
        {"pointer": "bad"},
        {"pointer": object()},
        {"parent_pointer": "bad"},
        {"parent_pointer": object()},
        {"name": object()},
        {"kind": object()},
        {"child_count": -1},
        {"child_count": True},
        {"preview": object()},
        {"preview_truncated": object()},
    )
    for changes in node_cases:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(node, **changes)

    event = sse_page.events[0]
    event_cases: tuple[dict[str, object], ...] = (
        {"ordinal": -1},
        {"data_line_count": -1},
        {"data_character_count": True},
        {"event_type": object()},
        {"event_id": object()},
        {"retry_milliseconds": -1},
        {"retry_milliseconds": True},
        {"data_format": object()},
        {"data_preview": object()},
        {"preview_truncated": object()},
        {"json_node_count": 0},
        {"json_node_count": True},
        {"data_format": StructuredSseDataFormat.TEXT},
        {"json_node_count": None},
    )
    for changes in event_cases:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(event, **changes)

    json_page_cases: tuple[dict[str, object], ...] = (
        {"source": object()},
        {"pointer": "bad"},
        {"pointer": object()},
        {"sse_event_index": -1},
        {"sse_event_index": True},
        {"start": -1},
        {"total_nodes": -1},
        {"nodes": []},
        {"nodes": (object(),)},
        {"start": 2, "total_nodes": 2},
        {"nodes": (node, node), "total_nodes": 1, "next_start": None},
        {"next_start": 1},
    )
    for changes in json_page_cases:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(json_page, **changes)

    sse_page_cases: tuple[dict[str, object], ...] = (
        {"source": object()},
        {"events": []},
        {"events": (object(),)},
        {"trailing_incomplete_event": object()},
    )
    for changes in sse_page_cases:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(sse_page, **changes)


def test_json_scalar_pointer_escape_array_and_preview_edges(tmp_path: Path) -> None:
    root, reference = _bundle(
        tmp_path,
        b'{"a/b~c":[null,true,false,1.25,{"none":null}],"zero":0}',
        "application/json",
    )
    page = _read(
        root,
        reference,
        StructuredBodyQuery(
            format=StructuredBodyFormat.JSON,
            pointer="/a~1b~0c",
            start=0,
            maximum_items=10,
        ),
    )
    assert type(page) is StructuredJsonPage
    assert {node.kind for node in page.nodes} >= {
        StructuredJsonKind.NULL,
        StructuredJsonKind.BOOLEAN,
        StructuredJsonKind.DECIMAL,
    }
    assert page.nodes[-1].pointer.endswith("/none")

    selected = _read(
        root,
        reference,
        StructuredBodyQuery(
            format=StructuredBodyFormat.JSON,
            pointer="/a~1b~0c/0",
            start=0,
            maximum_items=1,
        ),
    )
    assert type(selected) is StructuredJsonPage
    assert selected.nodes[0].preview == "null"

    one_character = _read(
        root,
        reference,
        StructuredBodyQuery(
            format=StructuredBodyFormat.JSON,
            pointer="/a~1b~0c",
            start=0,
            maximum_items=1,
        ),
        limits=_projection_limits(maximum_preview_characters=1),
    )
    assert type(one_character) is StructuredJsonPage
    assert one_character.nodes[0].preview == "…"

    for pointer in ("/a~1b~0c/01", "/a~1b~0c/9", "/zero/0"):
        with pytest.raises(ArtifactOperationError) as captured:
            _read(
                root,
                reference,
                StructuredBodyQuery(
                    format=StructuredBodyFormat.JSON,
                    pointer=pointer,
                    start=0,
                    maximum_items=1,
                ),
            )
        assert captured.value.code is ArtifactOperationErrorCode.BODY_SUBTREE_NOT_FOUND


@pytest.mark.parametrize(
    "payload",
    [
        b"NaN",
        b"\xff",
        b'"\\ud800"',
        b'{"\\ud800":1}',
    ],
)
def test_json_rejects_nonstandard_numbers_bytes_and_non_scalar_unicode(
    tmp_path: Path,
    payload: bytes,
) -> None:
    root, reference = _bundle(tmp_path, payload, "application/json")
    with pytest.raises(ArtifactOperationError) as captured:
        _read(
            root,
            reference,
            StructuredBodyQuery(
                format=StructuredBodyFormat.JSON,
                start=0,
                maximum_items=5,
            ),
        )
    assert captured.value.code is ArtifactOperationErrorCode.BODY_DECODE_FAILED


def test_structured_read_limits_complete_capture_and_top_level_sse_pointer(
    tmp_path: Path,
) -> None:
    root, reference = _bundle(tmp_path, b'{"a":1}', "application/json")
    query = StructuredBodyQuery(
        format=StructuredBodyFormat.JSON,
        pointer="/a",
        start=0,
        maximum_items=1,
    )
    with pytest.raises(TypeError, match="StructuredBodyLimits"):
        asyncio.run(
            read_artifact_structured_body_page(
                root,
                "artifact-0",
                reference,
                read_limits(),
                cast("StructuredBodyLimits", object()),
                query,
            )
        )
    with pytest.raises(TypeError, match="StructuredBodyQuery"):
        asyncio.run(
            read_artifact_structured_body_page(
                root,
                "artifact-0",
                reference,
                read_limits(),
                _projection_limits(),
                cast("StructuredBodyQuery", object()),
            )
        )
    with pytest.raises(ArtifactOperationError) as pointer_limit:
        _read(root, reference, query, limits=_projection_limits(maximum_pointer_bytes=1))
    assert pointer_limit.value.code is ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED
    with pytest.raises(ArtifactOperationError) as document_limit:
        _read(root, reference, query, limits=_projection_limits(maximum_document_bytes=2))
    assert document_limit.value.code is ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED

    with pytest.raises(ArtifactOperationError) as missing:
        bodies._complete_body(_window(total_bytes=None, content=b""))
    assert missing.value.code is ArtifactOperationErrorCode.BODY_NOT_FOUND
    with pytest.raises(ArtifactOperationError) as incomplete:
        bodies._complete_body(_window(content=b"{", next_offset=1))
    assert incomplete.value.code is ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED

    sse_root, sse_reference = _bundle(tmp_path / "sse", b"data: ok\n\n", "text/event-stream")
    with pytest.raises(ArtifactOperationError) as top_pointer:
        _read(
            sse_root,
            sse_reference,
            StructuredBodyQuery(
                format=StructuredBodyFormat.SSE,
                pointer="/invalid",
                start=0,
                maximum_items=1,
            ),
        )
    assert top_pointer.value.code is ArtifactOperationErrorCode.INVALID_LIMIT


@pytest.mark.parametrize(
    ("payload", "changes"),
    [
        (b"\xff", {}),
        (b"data: 123456\n\n", {"maximum_sse_line_bytes": 3}),
        (b"data: one\n\ndata: two\n\n", {"maximum_sse_events": 1}),
        (b'data: {"a":[1,2]}\n\n', {"maximum_nodes": 2}),
    ],
)
def test_sse_decode_and_projection_limits_fail_closed(
    tmp_path: Path,
    payload: bytes,
    changes: dict[str, int],
) -> None:
    root, reference = _bundle(tmp_path, payload, "text/event-stream")
    with pytest.raises(ArtifactOperationError) as captured:
        _read(
            root,
            reference,
            StructuredBodyQuery(
                format=StructuredBodyFormat.SSE,
                start=0,
                maximum_items=10,
            ),
            limits=_projection_limits(**changes),
        )
    assert captured.value.code in {
        ArtifactOperationErrorCode.BODY_DECODE_FAILED,
        ArtifactOperationErrorCode.BODY_PROJECTION_LIMIT_EXCEEDED,
    }


def test_sse_ignores_unknown_invalid_controls_and_pages_empty_ranges(tmp_path: Path) -> None:
    payload = (
        b"id: accepted\n"
        b"id: ignored\x00value\n"
        b"retry: nope\n"
        b"retry: 1\xc2\xb2\n"
        b"unknown: field\n"
        b"data:value\n\n"
    )
    root, reference = _bundle(tmp_path, payload, "text/event-stream")
    page = _read(
        root,
        reference,
        StructuredBodyQuery(
            format=StructuredBodyFormat.SSE,
            start=4,
            maximum_items=2,
        ),
    )
    assert type(page) is StructuredSsePage
    assert page.total_events == 1
    assert page.events == ()
    assert page.next_start is None


def test_array_index_helper_rejects_non_ascii_and_out_of_range_tokens() -> None:
    assert bodies._array_index("²", 2) is None
    assert bodies._array_index("x", 2) is None
    assert bodies._array_index("2", 2) is None
    assert bodies._array_index("1", 2) == 1
