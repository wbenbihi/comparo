"""Standalone Value exploration resolves public data and never returns secret payloads."""

import asyncio
import json
from pathlib import Path

import msgspec

from comparo.application.value_inspection import inspect_value
from comparo.tui.backend import RESOLVED_PROJECTION_LIMITS
from comparo.tui.backend import TuiBackend


def _fixture(root: Path) -> TuiBackend:
    data = root / ".comparo"
    data.mkdir()

    def doc(kind: str, ident: str, spec: dict[str, object]) -> str:
        return json.dumps(
            {
                "apiVersion": "comparo/v1alpha1",
                "kind": kind,
                "metadata": {"id": ident, "name": ident},
                "spec": spec,
            }
        )

    (root / "comparo.yaml").write_text(
        doc(
            "Project",
            "values",
            {
                "data": ".comparo",
                "variables": {"label": "public-label"},
                "secrets": {"token": "secret-value-canary"},
            },
        )
    )
    (data / "values.yaml").write_text(
        "\n---\n".join(
            [
                doc("Value", "base", {"value": {"name": "public-name", "count": 42}}),
                doc(
                    "Value",
                    "example",
                    {
                        "value": {
                            "nested": [{"enabled": True}, {"$use": "Value/base"}],
                            "label": {"$var": "label"},
                            "token": {"$secret": "token"},
                            "derived": "Bearer ${secrets.token}",
                            "absent": {"$matrix": "region"},
                            "fallback": {"$from": [{"$env": "ABSENT"}, "fallback-value"]},
                        }
                    },
                ),
            ]
        )
    )
    return TuiBackend(root)


def test_values_resolve_without_requests_scenarios_or_environments(tmp_path: Path) -> None:
    async def run() -> None:
        backend = _fixture(tmp_path)
        snapshot = backend.load_workspace().snapshot
        assert snapshot is not None
        resource = next(
            r.coordinate for r in snapshot.resources if r.coordinate.resource_id == "example"
        )
        result = await inspect_value(
            backend._workspace_request,
            resource,
            None,
            policy_path=None,
            policy_limits=backend.launch.limits.policy_load,
            settings=backend.execution_settings,
            limits=RESOLVED_PROJECTION_LIMITS,
        )
        assert result.diagnostics == ()
        nodes = {f.path: f.node for f in result.fields if f.node is not None}
        assert nodes[("value", "nested", "1", "name")].preview == "public-name"
        assert nodes[("value", "nested", "1", "count")].preview == "42"
        assert nodes[("value", "label")].preview == "public-label"
        assert nodes[("value", "token")].masked
        assert nodes[("value", "derived")].masked
        assert any(f.path == ("value", "absent") and f.unavailable for f in result.fields)
        serialized = msgspec.json.encode(result).decode()
        assert "secret-value-canary" not in serialized
        assert "public-name" in serialized
        await backend.shutdown()

    asyncio.run(run())
