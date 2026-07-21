"""Parse a streamed response body into its ordered records.

A streaming endpoint delivers a sequence — Server-Sent Events, or a chunked run
of JSON objects — rather than one payload. This turns the assembled body back
into that list, so the diff can compare the sequence event-by-event instead of
flattening it to bytes.

This is THE stream parse: the Run tab's event renderer and the diff engine's
per-event comparison both consume :func:`parse_stream`'s output, so the two tabs
can never disagree about what an event contains.
"""

import json

#: ``id`` fields containing U+0000 are ignored per the SSE spec (they would
#: poison the Last-Event-ID header a real client echoes back).
_NUL = "\x00"


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
    r"""Parse a Server-Sent-Events body into an ordered list of field mappings.

    The full envelope survives, per the SSE processing model: ``id``, ``event``
    (absent when unnamed — a renderer shows the spec default *message*), ``data``
    (multiple ``data`` lines join with a newline before any JSON parsing), and
    ``retry`` (the reconnect hint — preserved verbatim, we record rather than
    reconnect). Also per spec: any line ending (``\r\n``/``\r``/``\n``), a
    leading BOM stripped, ``:`` comment lines ignored, a colon-less line as a
    field with an empty value, exactly one leading space after the colon
    stripped, later non-``data`` fields overwriting earlier ones, and an ``id``
    containing U+0000 ignored. One deliberate divergence: a trailing event with
    no terminating blank line is **kept**, not discarded — a stream cut by the
    idle/total timeout still diffs whatever arrived.

    Args:
        text: The decoded SSE body.

    Returns:
        One mapping per event, in order.
    """
    events: list[dict[str, str]] = []
    fields: dict[str, str] = {}
    for index, line in enumerate(text.splitlines()):
        if index == 0:
            line = line.removeprefix("\ufeff")
        if not line:
            # Only a truly empty line dispatches; a whitespace-only line is a
            # (weird but legal) field name like any other.
            if fields:
                events.append(fields)
                fields = {}
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "id" and _NUL in value:
            continue
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
