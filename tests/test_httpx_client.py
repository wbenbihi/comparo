"""Tests for the httpx adapter's body-encoding and auth mapping."""

import httpx

from comparo.adapters.httpx_client import _auth
from comparo.adapters.httpx_client import _encode_body
from comparo.core.resolve import ResolvedRequest


def _request(body: object = None, body_type: str = "json") -> ResolvedRequest:
    return ResolvedRequest("POST", "https://x.test", [], {}, body, [], body_type=body_type)


def test_encode_body_routes_by_type() -> None:
    assert _encode_body(_request({"a": 1}, "json")) == ({"a": 1}, None, None)
    assert _encode_body(_request({"a": 1}, "form")) == (None, {"a": 1}, None)
    assert _encode_body(_request("hello", "raw")) == (None, None, "hello")
    assert _encode_body(_request(None)) == (None, None, None)


def test_auth_maps_basic_and_bearer() -> None:
    auth, header = _auth({"basic": {"username": "u", "password": "p"}})
    assert isinstance(auth, httpx.BasicAuth)
    assert header is None

    auth, header = _auth({"bearer": "tok"})
    assert auth is None
    assert header == ("Authorization", "Bearer tok")

    assert _auth(None) == (None, None)
