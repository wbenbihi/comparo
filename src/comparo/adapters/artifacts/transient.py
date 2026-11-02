"""Artifact body-reader adapter over the production transient HTTP body store."""

from __future__ import annotations

from collections.abc import AsyncIterable

from comparo.adapters.http.storage import TransientBodyStore
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.response_bodies import BodyHandle


class TransientArtifactBodyReader:
    """Expose locator-free request and response reads during artifact capture."""

    __slots__ = ("_store",)

    def __init__(self, store: TransientBodyStore) -> None:
        """Bind the exact live store without taking cleanup ownership."""
        if type(store) is not TransientBodyStore:
            raise TypeError("artifact body reader requires TransientBodyStore")
        self._store = store

    def read(self, source: RepeatableByteSource | BodyHandle) -> AsyncIterable[bytes]:
        """Open the typed store reader without exposing its sensitive locator."""
        if type(source) is RepeatableByteSource:
            return self._store.open_request(source)
        if type(source) is BodyHandle:
            return self._store.open_response(source)
        raise TypeError("artifact body reader requires a request source or response handle")
