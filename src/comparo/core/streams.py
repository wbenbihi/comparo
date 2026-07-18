"""Parse a streamed response body into its ordered records.

A streaming endpoint delivers a sequence — Server-Sent Events, or a chunked run
of JSON objects — rather than one payload. This turns the assembled body back
into that list, so the diff can compare the sequence event-by-event instead of
flattening it to bytes.
"""

import json


def parse_stream(body: bytes, content_type: str) -> list[object]:
    """Split a streamed body into its ordered records.

    Args:
        body: The fully-read stream body.
        content_type: The response content type, used to pick the format.

    Returns:
        SSE events (as field mappings) for an event stream, the JSON objects of a
        chunked JSON stream, or the whole text as a single record otherwise.
    """
    text = body.decode("utf-8", errors="replace")
    if "event-stream" in content_type.lower():
        return list(parse_sse(text))
    records = _parse_json_stream(text)
    return records if records else [text]


def parse_sse(text: str) -> list[dict[str, str]]:
    """Parse a Server-Sent-Events body into an ordered list of field mappings.

    Each event is a ``{field: value}`` mapping; multiple ``data`` lines join with
    a newline, a leading space after the colon is stripped, and a ``:`` comment
    line is ignored. Every field name is preserved.

    Args:
        text: The decoded SSE body.

    Returns:
        One mapping per event, in order.
    """
    events: list[dict[str, str]] = []
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if fields:
                events.append(fields)
                fields = {}
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "data" and "data" in fields:
            fields["data"] = f"{fields['data']}\n{value}"
        else:
            fields[name] = value
    if fields:
        events.append(fields)
    return events


def _parse_json_stream(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    records: list[object] = []
    index, length = 0, len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            obj, end = decoder.raw_decode(text, index)
        except ValueError:
            return []  # not a JSON stream — let the caller fall back
        records.append(obj)
        index = end
    return records
