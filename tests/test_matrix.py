"""Tests for matrix expansion and injection."""

from pathlib import Path

from comparo.core.loader import load_project
from comparo.core.matrix import expand
from comparo.core.models import Request
from comparo.core.resolve import Resolver
from comparo.core.resolve import select_environment

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_expand_one_cell_per_case() -> None:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.echo-anything"]
    assert isinstance(request, Request)
    cells = expand(loaded, request)
    assert len(cells) == 3
    assert any("locale=en-US" in cell.key for cell in cells)


def test_expand_without_matrix_is_single_empty_cell() -> None:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    cells = expand(loaded, request)
    assert len(cells) == 1
    assert cells[0].key == ""


def test_injection_merges_into_query() -> None:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.echo-anything"]
    assert isinstance(request, Request)
    cell = next(cell for cell in expand(loaded, request) if "ja-JP" in cell.key)
    resolved = Resolver(loaded, env).resolve_request(request, cell)
    assert resolved.query["locale"] == "ja-JP"
    assert resolved.query["currency"] == "JPY"
