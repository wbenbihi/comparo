"""Closed built-in static selector field catalog."""

from __future__ import annotations

from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.value_kinds import ValueKind

type StaticFieldDefinition = tuple[SelectorSource, StaticFieldPath, ValueKind]


STATIC_FIELD_CATALOG: tuple[StaticFieldDefinition, ...] = (
    (SelectorSource.EXECUTION, StaticFieldPath(segments=("execution", "id")), ValueKind.STRING),
    (
        SelectorSource.EXECUTION,
        StaticFieldPath(segments=("execution", "timings", "queue")),
        ValueKind.DURATION,
    ),
    (
        SelectorSource.EXECUTION,
        StaticFieldPath(segments=("execution", "timings", "elapsed")),
        ValueKind.DURATION,
    ),
    (SelectorSource.ATTEMPT, StaticFieldPath(segments=("attempt", "id")), ValueKind.STRING),
    (
        SelectorSource.ATTEMPT,
        StaticFieldPath(segments=("attempt", "timings", "elapsed")),
        ValueKind.DURATION,
    ),
    (SelectorSource.EXCHANGE, StaticFieldPath(segments=("exchange", "id")), ValueKind.STRING),
    *(
        (
            SelectorSource.RESPONSE_TIMINGS,
            StaticFieldPath(segments=("response", "timings", name)),
            ValueKind.DURATION,
        )
        for name in (
            "connection-acquisition",
            "dns",
            "connect",
            "proxy-tunnel",
            "tls",
            "request-write",
            "response-headers",
            "server-wait",
            "first-body-byte",
            "body-read",
            "elapsed",
        )
    ),
    (
        SelectorSource.RESPONSE_ENDPOINT,
        StaticFieldPath(segments=("response", "endpoint", "remote-address")),
        ValueKind.STRING,
    ),
    (
        SelectorSource.RESPONSE_ENDPOINT,
        StaticFieldPath(segments=("response", "endpoint", "remote-port")),
        ValueKind.INTEGER,
    ),
    (
        SelectorSource.RESPONSE_ENDPOINT,
        StaticFieldPath(segments=("response", "endpoint", "connection-id")),
        ValueKind.STRING,
    ),
    (
        SelectorSource.RESPONSE_ENDPOINT,
        StaticFieldPath(segments=("response", "endpoint", "connection-reused")),
        ValueKind.BOOLEAN,
    ),
    *(
        (SelectorSource.RESPONSE_TLS, StaticFieldPath(segments=path), kind)
        for side in ("connect", "origin")
        for leaf, kind in (
            ("version", ValueKind.STRING),
            ("cipher-suite", ValueKind.STRING),
            ("alpn", ValueKind.STRING),
            ("session-resumed", ValueKind.BOOLEAN),
        )
        for path in (("response", "tls", side, leaf),)
    ),
    (
        SelectorSource.STREAM_TIMINGS,
        StaticFieldPath(segments=("stream", "timings", "time-to-first-record")),
        ValueKind.DURATION,
    ),
    (
        SelectorSource.STREAM_TIMINGS,
        StaticFieldPath(segments=("stream", "timings", "observation-window-elapsed")),
        ValueKind.DURATION,
    ),
    (
        SelectorSource.STREAM_TERMINATION,
        StaticFieldPath(segments=("stream", "termination", "reason")),
        ValueKind.STRING,
    ),
    (
        SelectorSource.STREAM_TERMINATION,
        StaticFieldPath(segments=("stream", "termination", "completeness")),
        ValueKind.STRING,
    ),
)

STATIC_FIELD_REQUIRED_SOURCES = frozenset(
    {
        SelectorSource.RESPONSE_TIMINGS,
        SelectorSource.RESPONSE_ENDPOINT,
        SelectorSource.RESPONSE_TLS,
        SelectorSource.STREAM_TIMINGS,
    }
)


def static_field_kind(source: SelectorSource, path: StaticFieldPath) -> ValueKind | None:
    """Return the exact built-in value kind, or none for an unsupported field."""
    return next(
        (
            kind
            for candidate_source, candidate_path, kind in STATIC_FIELD_CATALOG
            if candidate_source is source and candidate_path == path
        ),
        None,
    )


__all__ = (
    "STATIC_FIELD_CATALOG",
    "STATIC_FIELD_REQUIRED_SOURCES",
    "StaticFieldDefinition",
    "static_field_kind",
)
