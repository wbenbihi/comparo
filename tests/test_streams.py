"""Tests for parsing a streamed response into its ordered records."""

from comparo.core.streams import parse_sse
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


def test_parse_sse_handles_crlf_and_cr_line_endings() -> None:
    # The spec admits \r\n, \r, and \n; values must not keep a trailing \r.
    events = parse_sse("data: a\r\nid: 1\r\n\r\ndata: b\r\r")
    assert events == [{"data": "a", "id": "1"}, {"data": "b"}]


def test_parse_sse_strips_a_leading_bom() -> None:
    events = parse_sse("﻿data: x\n\n")
    assert events == [{"data": "x"}]


def test_parse_sse_colonless_line_is_a_field_with_empty_value() -> None:
    events = parse_sse("data\n\n")
    assert events == [{"data": ""}]


def test_parse_sse_multiple_data_lines_join_before_any_parsing() -> None:
    events = parse_sse("data: {\ndata: }\n\n")
    assert events == [{"data": "{\n}"}]


def test_parse_sse_later_event_field_overwrites_earlier() -> None:
    events = parse_sse("event: a\nevent: b\ndata: x\n\n")
    assert events == [{"event": "b", "data": "x"}]


def test_parse_sse_ignores_an_id_containing_nul() -> None:
    events = parse_sse("id: bad\x00id\ndata: x\n\n")
    assert events == [{"data": "x"}]


def test_parse_sse_comment_only_block_dispatches_nothing() -> None:
    assert parse_sse(": keepalive\n\n: another\n\n") == []


def test_parse_sse_keeps_a_trailing_unterminated_event() -> None:
    # Deliberate divergence from the spec's discard rule: a stream cut by the
    # idle/total timeout still diffs whatever arrived.
    events = parse_sse("data: a\n\ndata: cut")
    assert events == [{"data": "a"}, {"data": "cut"}]


def test_parse_sse_whitespace_only_line_is_not_a_dispatch() -> None:
    # Only a truly empty line dispatches; " " is a (weird but legal) field name,
    # so both data lines still belong to ONE event.
    events = parse_sse("data: a\n \ndata: b\n\n")
    assert len(events) == 1
    assert events[0]["data"] == "a\nb"
