"""Load authenticated bounded body previews for the shared Analyze component."""

from typing import TYPE_CHECKING

from comparo.tui.backend import TuiBackend
from comparo.tui.models.evidence import body_preview

if TYPE_CHECKING:
    from comparo.tui.components.results import ResultsView


async def read_preview(backend: TuiBackend, event: "ResultsView.BodyRequested") -> None:
    """Keep body read failures value-free and retain the exact selection coordinate."""
    try:
        windows = [await backend.read_result_body(reference) for reference in event.references]
    except Exception:  # noqa: BLE001 - authenticated read failures remain value-free
        event.view.show_body(
            event.references, None, "Capture unavailable or report verification failed."
        )
        return
    document, message = body_preview(windows)
    event.view.show_body(event.references, document, message)
