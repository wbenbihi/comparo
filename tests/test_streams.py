"""Tests for parsing a streamed response into its ordered records."""

from comparo.core.streams import parse_stream


def test_parse_sse_events() -> None:
    body = b"event: message\ndata: hello\n\ndata: line1\ndata: line2\n\n"
    assert parse_stream(body, "text/event-stream") == [
        {"event": "message", "data": "hello"},
        {"data": "line1\nline2"},
    ]


def test_parse_sse_captures_id_retry_and_ignores_comments() -> None:
    # A full SSE frame carries id and event alongside data, and may set retry;
    # `:` comment lines are ignored. comparo keeps every field per the spec.
    body = (
        b"id: 1\nevent: tick\ndata: hi\n\n"
        b": a comment line is skipped\n"
        b"id: 2\nevent: done\nretry: 3000\ndata: bye\n\n"
    )
    assert parse_stream(body, "text/event-stream") == [
        {"id": "1", "event": "tick", "data": "hi"},
        {"id": "2", "event": "done", "retry": "3000", "data": "bye"},
    ]


def test_parse_concatenated_json() -> None:
    assert parse_stream(b'{"n": 1}{"n": 2}{"n": 3}', "application/json") == [
        {"n": 1},
        {"n": 2},
        {"n": 3},
    ]


def test_parse_ndjson() -> None:
    assert parse_stream(b'{"a": 1}\n{"a": 2}\n', "application/x-ndjson") == [{"a": 1}, {"a": 2}]


def test_parse_falls_back_to_whole_text() -> None:
    assert parse_stream(b"just text", "text/plain") == ["just text"]
