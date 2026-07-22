"""Tests for the OpenAPI 3.x project importer (adapter + CLI command)."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from comparo.adapters import openapi
from comparo.cli.app import app
from comparo.core.loader import load_project
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.models import Schema

runner = CliRunner()

# A small but representative OpenAPI 3.1 document: two servers, three operations
# (a path param, a query param, and a JSON body), a 2xx `$ref` response, one
# component schema, and a bearer security scheme.
SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Widget API", "version": "2.0.0"},
    "servers": [
        {"url": "https://staging.example.com/api/", "description": "Staging"},
        {
            "url": "https://{region}.example.com",
            "description": "Production",
            "variables": {"region": {"default": "prod"}},
        },
    ],
    "components": {
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
        },
        "schemas": {
            "User": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "manager": {"$ref": "#/components/schemas/User"},
                },
            },
        },
    },
    "paths": {
        "/users/{id}": {
            "get": {
                "operationId": "getUser",
                "summary": "Fetch a user",
                "tags": ["users"],
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/User"}},
                        },
                    },
                },
            },
        },
        "/users": {
            "get": {
                "operationId": "listUsers",
                "summary": "List users",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}},
                ],
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "operationId": "createUser",
                "summary": "Create a user",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/User"}},
                    },
                },
                "responses": {"201": {"description": "created"}},
            },
        },
    },
}


def _import_to(tmp_path: Path, spec: dict[str, Any]) -> Path:
    """Run the importer over *spec* into a fresh directory and return the manifest."""
    out = tmp_path / "project"
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    result = runner.invoke(app, ["import", "openapi", str(spec_file), "--output", str(out)])
    assert result.exit_code == 0, result.output
    return out / "comparo.yaml"


def test_import_produces_a_loadable_project(tmp_path: Path) -> None:
    manifest = _import_to(tmp_path, SPEC)
    loaded = load_project(manifest)  # the key end-to-end check: it loads and validates

    environments = [o for o in loaded.objects.values() if isinstance(o, Environment)]
    requests = [o for o in loaded.objects.values() if isinstance(o, Request)]
    schemas = [o for o in loaded.objects.values() if isinstance(o, Schema)]
    assert len(environments) == 2
    assert len(requests) == 3
    assert len(schemas) == 1


def test_no_diff_profile_is_generated(tmp_path: Path) -> None:
    manifest = _import_to(tmp_path, SPEC)
    loaded = load_project(manifest)
    assert not any(isinstance(o, DiffProfile) for o in loaded.objects.values())


def test_bearer_scheme_becomes_a_secret_ref_with_no_credential(tmp_path: Path) -> None:
    manifest = _import_to(tmp_path, SPEC)
    loaded = load_project(manifest)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))

    # Auth is a masked secret hole, and the secret is sourced from $env (a placeholder).
    assert environment.spec.auth == {"bearer": "${API_TOKEN}"}
    assert environment.spec.secrets == {"API_TOKEN": {"$env": "API_TOKEN"}}

    # No real credential is written anywhere in the scaffold — only refs/holes.
    for path in (manifest.parent / ".comparo").rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "bearerFormat" not in text
        assert "JWT" not in text


def test_endpoints_bodies_and_response_schema_ref(tmp_path: Path) -> None:
    manifest = _import_to(tmp_path, SPEC)
    loaded = load_project(manifest)
    get_user = loaded.objects["request.getuser"]
    list_users = loaded.objects["request.listusers"]
    create_user = loaded.objects["request.createuser"]
    assert isinstance(get_user, Request)
    assert isinstance(list_users, Request)
    assert isinstance(create_user, Request)

    # Path params become ${...} holes; the 2xx $ref response maps to a comparo Schema.
    assert get_user.spec.request.method == "GET"
    assert get_user.spec.request.endpoint == "/users/${id}"
    assert get_user.spec.response is not None
    assert get_user.spec.response.status == 200
    assert get_user.spec.response.schema == {"$use": "schema.user"}

    # A query parameter becomes a query entry with its example/default value.
    assert list_users.spec.request.query == {"limit": 20}

    # A JSON requestBody schema becomes a body stub (recursion is bounded).
    assert isinstance(create_user.spec.request.body, dict)
    assert create_user.spec.request.body["name"] == "string"


def test_servers_become_a_diff_pair(tmp_path: Path) -> None:
    manifest = _import_to(tmp_path, SPEC)
    loaded = load_project(manifest)
    assert loaded.project is not None
    environments = loaded.project.spec.environments
    assert environments is not None
    pairs = environments.diff_pairs
    assert pairs is not None
    assert [(pair.name, pair.baseline, pair.candidate) for pair in pairs] == [
        ("staging-vs-production", "staging", "production")
    ]

    # Server template variables and trailing slashes are resolved in the base URLs.
    urls = {o.spec.base_url for o in loaded.objects.values() if isinstance(o, Environment)}
    assert urls == {"https://staging.example.com/api", "https://prod.example.com"}


def test_spec_without_servers_still_loads(tmp_path: Path) -> None:
    spec = {**SPEC}
    spec.pop("servers")
    manifest = _import_to(tmp_path, spec)
    loaded = load_project(manifest)
    environments = [o for o in loaded.objects.values() if isinstance(o, Environment)]
    assert len(environments) == 1
    assert environments[0].spec.base_url == "https://example.com"


def test_swagger_2_is_rejected() -> None:
    with pytest.raises(openapi.OpenApiImportError, match=r"Swagger 2\.0 is not supported"):
        openapi.import_openapi({"swagger": "2.0", "info": {"title": "old"}, "paths": {}})


def test_swagger_2_cli_reports_a_clear_error(tmp_path: Path) -> None:
    spec_file = tmp_path / "swagger.json"
    spec_file.write_text(json.dumps({"swagger": "2.0", "paths": {}}), encoding="utf-8")
    result = runner.invoke(
        app, ["import", "openapi", str(spec_file), "--output", str(tmp_path / "out")]
    )
    assert result.exit_code == 2  # a usage error
    assert "Swagger 2.0 is not supported" in result.output


def test_non_openapi_document_is_rejected() -> None:
    with pytest.raises(openapi.OpenApiImportError, match=r"OpenAPI 3\.x"):
        openapi.import_openapi({"info": {"title": "x"}})


def test_import_refuses_to_overwrite(tmp_path: Path) -> None:
    manifest = _import_to(tmp_path, SPEC)
    spec_file = tmp_path / "spec.json"  # written by the first import
    again = runner.invoke(
        app, ["import", "openapi", str(spec_file), "--output", str(manifest.parent)]
    )
    assert again.exit_code == 2  # a usage error
    assert "refusing" in again.output


def test_load_spec_accepts_yaml() -> None:
    text = """
    openapi: 3.0.3
    info:
      title: YAML API
    paths: {}
    """
    parsed = openapi.load_spec(text)
    result = openapi.import_openapi(parsed)
    assert result.project_name == "YAML API"
    assert len(result.environments) == 1  # placeholder, no servers declared


def test_basic_auth_declares_both_username_and_password_secrets() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Basic API"},
        "components": {
            "securitySchemes": {
                "basicAuth": {"type": "http", "scheme": "basic"},
            },
        },
        "paths": {},
    }
    result = openapi.import_openapi(spec)
    environment = result.environments[0].document["spec"]
    # The auth stub references both holes, so both secrets must be declared.
    assert environment["auth"] == {
        "basic": {"username": "${API_USERNAME}", "password": "${API_PASSWORD}"}
    }
    assert environment["secrets"] == {
        "API_PASSWORD": {"$env": "API_PASSWORD"},
        "API_USERNAME": {"$env": "API_USERNAME"},
    }
    assert result.secret_env_vars == ["API_PASSWORD", "API_USERNAME"]


def test_path_params_become_holes_backed_by_environment_stubs(tmp_path: Path) -> None:
    manifest = _import_to(tmp_path, SPEC)
    loaded = load_project(manifest)
    get_user = loaded.objects["request.getuser"]
    assert isinstance(get_user, Request)
    assert get_user.spec.request.endpoint == "/users/${id}"

    # Every environment declares a same-named stub variable, so ${id} resolves.
    for environment in (o for o in loaded.objects.values() if isinstance(o, Environment)):
        assert environment.spec.variables == {"id": "0"}


def test_apikey_query_scheme_wires_a_query_param() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Query-keyed API"},
        "components": {
            "securitySchemes": {
                "key": {"type": "apiKey", "in": "query", "name": "api_key"},
            },
        },
        "paths": {
            "/things": {
                "get": {"operationId": "listThings", "responses": {"200": {"description": "ok"}}},
            },
        },
    }
    result = openapi.import_openapi(spec)
    request = result.requests[0].document["spec"]["request"]
    assert request["query"] == {"api_key": "${API_KEY}"}
    environment = result.environments[0].document["spec"]
    assert environment["secrets"] == {"API_KEY": {"$env": "API_KEY"}}


def test_apikey_cookie_scheme_wires_a_cookie() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Cookie-keyed API"},
        "components": {
            "securitySchemes": {
                "key": {"type": "apiKey", "in": "cookie", "name": "session"},
            },
        },
        "paths": {
            "/things": {
                "get": {"operationId": "listThings", "responses": {"200": {"description": "ok"}}},
            },
        },
    }
    result = openapi.import_openapi(spec)
    request = result.requests[0].document["spec"]["request"]
    assert request["cookies"] == {"session": "${API_KEY}"}
    assert "query" not in request


def test_form_only_request_body_becomes_a_form_body() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Form API"},
        "paths": {
            "/login": {
                "post": {
                    "operationId": "login",
                    "requestBody": {
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "username": {"type": "string"},
                                        "attempts": {"type": "integer"},
                                    },
                                },
                            },
                        },
                    },
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    result = openapi.import_openapi(spec)
    request = result.requests[0].document["spec"]["request"]
    assert request["body"] == {"username": "string", "attempts": 0}
    assert request["bodyType"] == "form"


def test_cross_referencing_schemas_have_no_dangling_component_pointers(tmp_path: Path) -> None:
    spec = {
        **SPEC,
        "components": {
            **SPEC["components"],
            "schemas": {
                "User": SPEC["components"]["schemas"]["User"],
                "Team": {
                    "type": "object",
                    "properties": {
                        "lead": {"$ref": "#/components/schemas/User"},
                        "members": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/User"},
                        },
                    },
                },
            },
        },
    }
    manifest = _import_to(tmp_path, spec)
    loaded = load_project(manifest)
    team = loaded.objects["schema.team"]
    user = loaded.objects["schema.user"]
    assert isinstance(team, Schema)
    assert isinstance(user, Schema)

    # Referenced components are inlined under $defs and pointers made local, so
    # neither standalone Schema document contains a dangling #/components ref.
    assert team.spec["properties"]["lead"] == {"$ref": "#/$defs/User"}
    assert team.spec["$defs"]["User"]["properties"]["manager"] == {"$ref": "#/$defs/User"}
    assert user.spec["properties"]["manager"] == {"$ref": "#"}
    assert "#/components" not in json.dumps(team.spec)
    assert "#/components" not in json.dumps(user.spec)


def test_external_path_ref_is_skipped_with_a_warning() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Split API"},
        "paths": {
            "/users": {"$ref": "./paths/users.yaml"},
        },
    }
    result = openapi.import_openapi(spec)
    assert result.requests == []
    assert len(result.warnings) == 1
    assert "./paths/users.yaml" in result.warnings[0]
    assert "/users" in result.warnings[0]


def test_apikey_header_scheme_becomes_a_header_secret() -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Keyed API"},
        "components": {
            "securitySchemes": {
                "key": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            },
        },
        "paths": {},
    }
    result = openapi.import_openapi(spec)
    environment = result.environments[0].document["spec"]
    assert environment["headers"] == [{"key": "X-API-Key", "value": "${API_KEY}"}]
    assert environment["secrets"] == {"API_KEY": {"$env": "API_KEY"}}
    assert "API_KEY" in result.secret_env_vars
