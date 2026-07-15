"""Tests for the request resolver: refs, masking, and header merge."""

from pathlib import Path

from comparo.core.loader import load_project
from comparo.core.models import Request
from comparo.core.provenance import Origin
from comparo.core.resolve import Resolver
from comparo.core.resolve import select_environment

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_resolve_masks_secret_header() -> None:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.echo-anything"]
    assert isinstance(request, Request)
    resolved = Resolver(loaded, env).resolve_request(request)
    assert dict(resolved.headers)["authorization"] == "Bearer ••••••"
    assert any(entry.origin is Origin.SECRET for entry in resolved.trail)


def test_resolve_interpolates_variable_in_body() -> None:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.echo-anything"]
    assert isinstance(request, Request)
    resolved = Resolver(loaded, env).resolve_request(request)
    body = resolved.body
    assert isinstance(body, dict)
    order = body["order"]
    assert isinstance(order, dict)
    assert order["note"] == "Locale is en-US"


def test_resolve_builds_url() -> None:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    resolved = Resolver(loaded, env).resolve_request(request)
    assert resolved.url == "http://localhost:8080/json"


def test_select_environment_by_id_and_name() -> None:
    loaded = load_project(SAMPLE)
    assert select_environment(loaded, "prod").metadata.id == "environment.prod"
    assert select_environment(loaded, "environment.prod").metadata.id == "environment.prod"


def test_header_merge_request_wins(tmp_path: Path) -> None:
    (tmp_path / "environments").mkdir()
    (tmp_path / "requests").mkdir()
    (tmp_path / "environments" / "e.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: E\n  id: environment.e\n"
        "spec:\n  baseUrl: http://x\n"
        "  headers:\n    - key: x-source\n      value: env\n"
        "    - key: x-only-env\n      value: keep\n"
    )
    (tmp_path / "requests" / "r.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: R\n  id: request.r\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        "    headers:\n      - key: x-source\n        value: req\n"
    )
    loaded = load_project(tmp_path)
    env = select_environment(loaded, "environment.e")
    request = loaded.objects["request.r"]
    assert isinstance(request, Request)
    headers = dict(Resolver(loaded, env).resolve_request(request).headers)
    assert headers["x-source"] == "req"
    assert headers["x-only-env"] == "keep"
