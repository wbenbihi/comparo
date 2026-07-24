"""Sensitivity-lineage projection for typed HTTP request bodies."""

from __future__ import annotations

from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value


def _value_origins(value: object) -> tuple[TaintOriginId, ...]:
    """Return safe sensitivity lineage from a sealed value or byte-source descriptor."""
    if is_sensitive_value(value):
        return value.origins
    if type(value) is RepeatableByteSource:
        return value.taint_origins
    return ()


def request_body_taint_origins(body: RequestBody) -> tuple[TaintOriginId, ...]:
    """Return the stable union of every sensitive input contributing to a request body."""
    if type(body) is NoBody:
        return ()
    if type(body) is JsonBody:
        return _value_origins(body.value)
    if type(body) is TextBody:
        return _value_origins(body.value)
    if type(body) is BytesBody:
        return _value_origins(body.value)
    if type(body) is FormBody:
        origins = (origin for field in body.fields for origin in _value_origins(field.value))
        return tuple(dict.fromkeys(origins))
    if type(body) is MultipartBody:
        collected: list[TaintOriginId] = []
        for part in body.parts:
            if type(part) is MultipartTextPart:
                collected.extend(_value_origins(part.value))
                continue
            collected.extend(_value_origins(part.filename))
            collected.extend(_value_origins(part.value))
        return tuple(dict.fromkeys(collected))
    raise TypeError("request-body lineage requires an exact RequestBody")
