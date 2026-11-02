"""Cycle-free requested body-view and decoder compatibility rules."""

from __future__ import annotations

from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.selectors.sources import BodyViewKind

_EXPLICIT_DECODER_KINDS = (
    (BodyViewKind.TEXT, ViewKind.TEXT),
    (BodyViewKind.JSON, ViewKind.JSON),
    (BodyViewKind.SSE_EVENTS, ViewKind.SSE),
    (BodyViewKind.NDJSON_RECORDS, ViewKind.NDJSON),
    (BodyViewKind.JSON_SEQUENCE_RECORDS, ViewKind.JSON_SEQUENCE),
)


def validate_requested_body_decoder(
    requested: BodyViewKind,
    decoder: DecoderSpec,
    auto_selection: AutoViewSelection | None,
) -> None:
    """Bind an explicit request or automatic provenance to its exact decoder."""
    if type(requested) is not BodyViewKind or type(decoder) is not DecoderSpec:
        raise TypeError("body-view provenance requires a typed request and decoder")
    if requested in {BodyViewKind.HANDLE, BodyViewKind.ENTITY_BYTES}:
        raise ValueError("handle and entity-byte selections are not decoded views")
    if requested is BodyViewKind.AUTO:
        if auto_selection is None:
            raise ValueError("automatic body views require automatic selection provenance")
        if type(auto_selection) is not AutoViewSelection or auto_selection.decoder != decoder:
            raise ValueError("automatic selection provenance contradicts its decoder")
        return
    if auto_selection is not None:
        raise ValueError("explicit body views cannot retain automatic selection provenance")
    expected = next(
        (kind for candidate, kind in _EXPLICIT_DECODER_KINDS if candidate is requested),
        None,
    )
    if expected is None or decoder.view is not expected:
        raise ValueError("requested body view contradicts its decoder")


__all__ = ("validate_requested_body_decoder",)
