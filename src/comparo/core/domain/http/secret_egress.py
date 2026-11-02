"""Sensitive lineage projection for destination-bound HTTP egress authority."""

from __future__ import annotations

from comparo.core.domain.http.request_materialization import request_envelope_taint_origins
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.sensitivity import TaintOriginId


def http_secret_egress_origins(request: FinalizedWireRequest) -> tuple[TaintOriginId, ...]:
    """Return every authorization-relevant sensitive origin in a finalized request."""
    if type(request) is not FinalizedWireRequest:
        raise TypeError("HTTP secret egress lineage requires FinalizedWireRequest")
    return request_envelope_taint_origins(
        request.url,
        request.headers,
        request.cookies,
        request.materialization.input_body.taint_origins,
    )


__all__ = ("http_secret_egress_origins",)
