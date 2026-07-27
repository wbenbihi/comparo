"""Windowed render-preview contract and boundary-failure coverage."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import msgspec
import pytest

from comparo.application import render_preview as rendering
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.render_preview import RenderPreviewRequest
from comparo.application.render_preview import RenderPreviewWindowRequest
from comparo.application.render_preview import RenderPreviewWindowResult
from comparo.application.render_preview import render_prepared_request_window
from comparo.application.render_preview import render_prepared_requests
from tests.application.test_render_preview import _preview
from tests.application.test_render_preview import _project
from tests.application.test_render_preview import _settings


def _window_request(tmp_path: Path) -> RenderPreviewWindowRequest:
    return RenderPreviewWindowRequest(
        preview=RenderPreviewRequest(project=_project(tmp_path), settings=_settings()),
        start_ordinal=0,
        maximum_previews=1,
    )


def test_window_request_rejects_untyped_or_unbounded_inputs(tmp_path: Path) -> None:
    request = _window_request(tmp_path)
    with pytest.raises(TypeError, match="RenderPreviewRequest"):
        msgspec.structs.replace(request, preview=object())
    for start in (-1, cast("int", True)):
        with pytest.raises(ValueError, match="start ordinal"):
            msgspec.structs.replace(request, start_ordinal=start)
    for maximum in (0, -1, cast("int", True)):
        with pytest.raises(ValueError, match="window size"):
            msgspec.structs.replace(request, maximum_previews=maximum)


def test_window_result_accepts_mixed_pages_and_rejects_invalid_products() -> None:
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="render")
    first = _preview()
    with pytest.raises(TypeError, match="products"):
        RenderPreviewWindowResult(previews=cast("Any", [first]), total_executions=1)
    with pytest.raises(ValueError, match="total"):
        RenderPreviewWindowResult(total_executions=-1)
    for continuation in (-1, 2, cast("int", True)):
        with pytest.raises(ValueError, match="continuation"):
            RenderPreviewWindowResult(total_executions=2, next_ordinal=continuation)
    with pytest.raises(TypeError, match="diagnostics"):
        RenderPreviewWindowResult(diagnostics=cast("Any", [diagnostic]))
    mixed = RenderPreviewWindowResult(
        previews=(first,),
        total_executions=2,
        next_ordinal=1,
        diagnostics=(diagnostic,),
    )
    assert mixed.previews == (first,)
    assert mixed.diagnostics == (diagnostic,)
    assert (
        RenderPreviewWindowResult(
            total_executions=2,
            next_ordinal=1,
            diagnostics=(diagnostic,),
        ).next_ordinal
        == 1
    )
    with pytest.raises(ValueError, match="unique and increasing"):
        RenderPreviewWindowResult(
            previews=(
                _preview(ordinal=1, execution_id="execution-1"),
                _preview(ordinal=0, execution_id="execution-0"),
            ),
            total_executions=3,
        )
    with pytest.raises(ValueError, match="unique and increasing"):
        RenderPreviewWindowResult(
            previews=(first, _preview(ordinal=0, execution_id="execution-2")),
            total_executions=3,
        )
    with pytest.raises(ValueError, match="exceeds"):
        RenderPreviewWindowResult(
            previews=(_preview(ordinal=2, execution_id="execution-2"),),
            total_executions=2,
        )
    with pytest.raises(ValueError, match="identities"):
        RenderPreviewWindowResult(
            previews=(first, _preview(ordinal=2)),
            total_executions=3,
        )

    gapped = RenderPreviewWindowResult(
        previews=(first, _preview(ordinal=2, execution_id="execution-2")),
        total_executions=3,
    )
    assert tuple(item.ordinal for item in gapped.previews) == (0, 2)

    assert RenderPreviewWindowResult(total_executions=0).previews == ()


def test_public_window_boundary_rejects_untyped_requests() -> None:
    with pytest.raises(TypeError, match="RenderPreviewWindowRequest"):
        asyncio.run(render_prepared_request_window(cast("RenderPreviewWindowRequest", object())))


def test_runtime_capability_diagnostics_short_circuit_both_preview_apis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = ApplicationDiagnostic(code="unsupported", severity="error", stage="planning")
    monkeypatch.setattr(
        rendering,
        "preflight_runtime_capabilities",
        lambda _project: SimpleNamespace(diagnostics=(object(),)),
    )
    monkeypatch.setattr(rendering, "inspect_diagnostic", lambda _diagnostic: diagnostic)
    window = _window_request(tmp_path)

    complete = asyncio.run(render_prepared_requests(window.preview))
    page = asyncio.run(render_prepared_request_window(window))

    assert complete.diagnostics == (diagnostic,)
    assert page.diagnostics == (diagnostic,)
    assert page.total_executions == 2


def test_windowed_preview_normalizes_internal_cleanup_and_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _window_request(tmp_path)

    async def explode(*_args: object) -> RenderPreviewWindowResult:
        raise RuntimeError("value-that-must-not-cross")

    async def close_ok(_self: object) -> bool:
        return False

    monkeypatch.setattr(rendering, "_render_window_with_resources", explode)
    monkeypatch.setattr(rendering._RenderResources, "close", close_ok)
    internal = asyncio.run(render_prepared_request_window(request))
    assert internal.diagnostics[0].code == "render.preview.internal"

    async def succeed(*_args: object) -> RenderPreviewWindowResult:
        return RenderPreviewWindowResult(
            previews=(_preview(),),
            total_executions=2,
            next_ordinal=1,
        )

    async def close_failed(_self: object) -> bool:
        return True

    monkeypatch.setattr(rendering, "_render_window_with_resources", succeed)
    monkeypatch.setattr(rendering._RenderResources, "close", close_failed)
    cleanup = asyncio.run(render_prepared_request_window(request))
    assert cleanup.diagnostics[0].code == "render.preview.cleanup-failed"

    closed = False

    async def cancelled(*_args: object) -> RenderPreviewWindowResult:
        raise asyncio.CancelledError

    async def close_cancelled(_self: object) -> bool:
        nonlocal closed
        closed = True
        return False

    monkeypatch.setattr(rendering, "_render_window_with_resources", cancelled)
    monkeypatch.setattr(rendering._RenderResources, "close", close_cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(render_prepared_request_window(request))
    assert closed


def test_window_resource_composition_retains_mixed_products_and_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _window_request(tmp_path)
    diagnostic = ApplicationDiagnostic(code="failed", severity="error", stage="resolution")

    async def products(*_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        return (_preview(),), (diagnostic,), 1

    monkeypatch.setattr(rendering, "_render_products", products)
    result = asyncio.run(
        rendering._render_window_with_resources(request, rendering._RenderResources())
    )

    assert result.previews == (_preview(),)
    assert result.diagnostics == (diagnostic,)
    assert result.next_ordinal == 1
