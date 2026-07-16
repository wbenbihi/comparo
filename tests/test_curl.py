"""Tests for rendering a resolved request as a curl command."""

from pathlib import Path

import pytest

from comparo.core.curl import to_curl
from comparo.core.loader import load_project
from comparo.core.matrix import expand
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_curl_renders_method_url_query_headers_and_body() -> None:
    resolved = ResolvedRequest(
        "POST",
        "https://api.test/orders",
        [("accept", "application/json")],
        {"locale": "en-US"},
        {"sku": "WIDGET-1"},
        [],
    )
    command = to_curl(resolved)
    assert command.startswith("curl -X POST 'https://api.test/orders?locale=en-US'")
    assert "-H 'accept: application/json'" in command
    assert "-H 'content-type: application/json'" in command  # added for a JSON body
    assert '--data \'{"sku": "WIDGET-1"}\'' in command


def test_curl_masks_secrets_but_reveals_them_under_the_execute_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPARO_DEMO_TOKEN", "real-token-abc")
    loaded = load_project(SAMPLE)
    objects = loaded.objects.values()
    request = next(o for o in objects if isinstance(o, Request) and o.spec.matrix)
    environment = next(
        o for o in objects if isinstance(o, Environment) and "local" not in o.metadata.name.lower()
    )
    cell = expand(loaded, request)[0]

    masked = to_curl(Resolver(loaded, environment, Sink.DISPLAY).resolve_request(request, cell))
    revealed = to_curl(Resolver(loaded, environment, Sink.EXECUTE).resolve_request(request, cell))

    assert "••••••" in masked
    assert "real-token-abc" in revealed
    assert "••••••" not in revealed
