"""Tests for the request resolver: refs, masking, and header merge."""

from pathlib import Path

from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.matrix import expand
from comparo.core.models import Request
from comparo.core.provenance import Origin
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink
from comparo.core.resolve import select_environment

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_matrix_injects_into_the_endpoint_path(tmp_path: Path) -> None:
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: E\n  id: environment.e\nspec:\n  baseUrl: https://api.test\n",
        encoding="utf-8",
    )
    (tmp_path / "codes.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Matrix\n"
        "metadata:\n  name: Codes\n  id: matrix.codes\n"
        "spec:\n  target: request.path\n  values:\n    - code: 200\n    - code: 404\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Status\n  id: request.status\n"
        "spec:\n  matrix:\n    - $use: matrix.codes\n"
        "  request:\n    method: GET\n    endpoint: /status/${code}\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    env = select_environment(loaded, "environment.e")
    request = loaded.objects["request.status"]
    assert isinstance(request, Request)
    resolver = Resolver(loaded, env)
    urls = sorted(resolver.resolve_request(request, cell).url for cell in expand(loaded, request))
    assert urls == ["https://api.test/status/200", "https://api.test/status/404"]


def test_resolve_carries_cookies(tmp_path: Path) -> None:
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: E\n  id: environment.e\nspec:\n  baseUrl: https://api.test\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: C\n  id: request.c\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        "    cookies:\n      session: abc\n      region: ${REGION|us}\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    env = select_environment(loaded, "environment.e")
    request = loaded.objects["request.c"]
    assert isinstance(request, Request)
    resolved = Resolver(loaded, env).resolve_request(request)
    assert resolved.cookies == {"session": "abc", "region": "us"}


def test_resolve_carries_body_type_and_masks_auth(tmp_path: Path) -> None:
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: E\n  id: environment.e\n"
        "spec:\n  baseUrl: https://api.test\n"
        "  secrets:\n    API_PASS:\n      $literal: s3cret\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Login\n  id: request.login\n"
        "spec:\n  request:\n    method: POST\n    endpoint: /login\n"
        "    bodyType: form\n    body:\n      user: bob\n"
        "    auth:\n      basic:\n        username: bob\n"
        "        password:\n          $secret: API_PASS\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    env = select_environment(loaded, "environment.e")
    request = loaded.objects["request.login"]
    assert isinstance(request, Request)

    display = Resolver(loaded, env).resolve_request(request)
    assert display.body_type == "form"
    display_auth = display.auth
    assert isinstance(display_auth, dict)
    basic = display_auth["basic"]
    assert isinstance(basic, dict)
    assert basic["password"] == "••••••"  # masked in the display sink

    execute = Resolver(loaded, env, Sink.EXECUTE).resolve_request(request)
    execute_auth = execute.auth
    assert isinstance(execute_auth, dict)
    execute_basic = execute_auth["basic"]
    assert isinstance(execute_basic, dict)
    assert execute_basic["password"] == "s3cret"  # real value for execution


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


# ── Phase 2: mapping headers, endpoint interpolation, pair guards, $val cycle ──


def _env_and_request(tmp_path: Path, request_yaml: str) -> tuple[LoadedProject, Request]:
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: E, id: environment.e}\n"
        "spec:\n  baseUrl: https://api.test\n"
        "  variables: {USER_ID: '42'}\n  secrets: {API_TOKEN: {$literal: tok}}\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(request_yaml, encoding="utf-8")
    loaded = load_project(tmp_path)
    request = loaded.objects["request.r"]
    assert isinstance(request, Request)
    return loaded, request


def test_mapping_form_headers_are_sent_and_interpolated(tmp_path: Path) -> None:
    loaded, request = _env_and_request(
        tmp_path,
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: R, id: request.r}\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        '    headers:\n      Authorization: "Bearer ${API_TOKEN}"\n',
    )
    env = select_environment(loaded, "environment.e")
    execute = Resolver(loaded, env, Sink.EXECUTE).resolve_request(request)
    assert execute.headers == [("Authorization", "Bearer tok")]
    display = Resolver(loaded, env, Sink.DISPLAY).resolve_request(request)
    assert display.headers == [("Authorization", "Bearer ••••••")]  # secret masked


def test_endpoint_interpolates_a_variable(tmp_path: Path) -> None:
    loaded, request = _env_and_request(
        tmp_path,
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: R, id: request.r}\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /users/${USER_ID}\n",
    )
    env = select_environment(loaded, "environment.e")
    resolved = Resolver(loaded, env, Sink.EXECUTE).resolve_request(request)
    assert resolved.url == "https://api.test/users/42"


def test_resolve_pair_rejects_a_lone_baseline_flag(tmp_path: Path) -> None:
    import pytest

    from comparo.core.resolve import EnvironmentSelectionError
    from comparo.core.resolve import resolve_pair

    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\nmetadata: {name: A, id: environment.a}\n"
        "spec: {baseUrl: 'http://a'}\n",
        encoding="utf-8",
    )
    (tmp_path / "b.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\nmetadata: {name: B, id: environment.b}\n"
        "spec: {baseUrl: 'http://b'}\n",
        encoding="utf-8",
    )
    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\nmetadata: {name: P, id: project.p}\n"
        "spec:\n  data: .\n  environments:\n    diffPairs:\n"
        "      - {name: p, baseline: a, candidate: b}\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path / "comparo.yaml")
    with pytest.raises(EnvironmentSelectionError, match="both --baseline and --candidate"):
        resolve_pair(loaded, None, "a", None)


def test_a_diffpair_with_a_typoed_key_is_a_load_error(tmp_path: Path) -> None:
    # The strict EnvironmentsConfig/DiffPair structs turn a mistyped diffPair key
    # into a hard load error, so a run can never silently gate the wrong pair.
    import pytest

    from comparo.core.diagnostics import LoadError

    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\nmetadata: {name: A, id: environment.a}\n"
        "spec: {baseUrl: 'http://a'}\n",
        encoding="utf-8",
    )
    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\nmetadata: {name: P, id: project.p}\n"
        "spec:\n  data: .\n  environments:\n    diffPairs:\n"
        "      - {name: p, baseline: a, candid: b}\n",  # 'candid' typo
        encoding="utf-8",
    )
    with pytest.raises(LoadError):
        load_project(tmp_path / "comparo.yaml")


def test_a_val_cycle_is_a_captured_error_not_a_recursion_crash() -> None:
    # Defense in depth: the loader now rejects a $val cycle up front, but if one
    # ever reached the resolver it must still fail closed with a captured error,
    # never a recursion crash. Build the cyclic project directly, past the loader.
    import msgspec
    import pytest

    from comparo.core.interpolation import InterpolationError
    from comparo.core.loader import LoadedProject
    from comparo.core.models import Environment
    from comparo.core.models import Instance
    from comparo.core.models import Object

    def instance(ident: str, ref: str) -> Instance:
        obj = msgspec.convert(
            {
                "apiVersion": "comparo/v1",
                "kind": "Instance",
                "metadata": {"name": ident, "id": ident},
                "spec": {"value": {"x": {"$val": ref}}},
            },
            type=Instance,
        )
        return obj

    env = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Environment",
            "metadata": {"name": "E", "id": "environment.e"},
            "spec": {"baseUrl": "http://h"},
        },
        type=Environment,
    )
    request = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Request",
            "metadata": {"name": "R", "id": "request.r"},
            "spec": {
                "request": {"method": "GET", "endpoint": "/x", "body": {"$val": "instance.a"}}
            },
        },
        type=Request,
    )
    objects: dict[str, Object] = {
        "instance.a": instance("instance.a", "instance.b"),
        "instance.b": instance("instance.b", "instance.a"),
        "environment.e": env,
        "request.r": request,
    }
    loaded = LoadedProject(root=Path(), project=None, objects=objects)
    with pytest.raises(InterpolationError, match="cycle"):
        Resolver(loaded, env, Sink.EXECUTE).resolve_request(request)


def test_literal_shields_a_ref_shaped_payload() -> None:
    # $literal returns its payload verbatim — a nested $use-shaped dict must be sent
    # as data, not resolved as a reference (else a literal `{"$use": ...}` body would
    # be silently rewritten).
    import msgspec

    request = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Request",
            "metadata": {"name": "R", "id": "request.r"},
            "spec": {
                "request": {
                    "method": "POST",
                    "endpoint": "/x",
                    "body": {
                        "$literal": {"$use": "diffprofile.nope", "keep": "${NOT_INTERPOLATED}"}
                    },
                }
            },
        },
        type=Request,
    )
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    resolved = Resolver(loaded, env, Sink.EXECUTE).resolve_request(request)
    # The whole payload is passed through untouched — the $use is not resolved and
    # the ${...} inside a literal is not interpolated.
    assert resolved.body == {"$use": "diffprofile.nope", "keep": "${NOT_INTERPOLATED}"}
