"""Display the same user-resolved shortcut identities used by Textual dispatch."""

from textual._context import active_app

from comparo.tui.preferences import SHORTCUTS
from comparo.tui.preferences import Preferences


def shortcut_label(command: str) -> str:
    """Resolve a visible key label from the active app's preference map."""
    try:
        preferences = getattr(active_app.get(), "preferences", None)
    except LookupError:
        preferences = None
    key = SHORTCUTS[command][1]
    if isinstance(preferences, Preferences):
        key = preferences.keymap.get(command, key)
    return {"left_square_bracket": "[", "right_square_bracket": "]"}.get(key, key)


def analysis_hint() -> str:
    """Use a compact default hint and enumerate only when view keys are customized."""
    lenses = ("issues", "requests", "rules", "fields", "timeline")
    keys = tuple(shortcut_label("analyze." + lens) for lens in lenses)
    views = (
        "Alt+1-5 views"
        if keys == tuple(f"alt+{i}" for i in range(1, 6))
        else " · ".join(f"{lens.title()} {key}" for lens, key in zip(lenses, keys, strict=True))
    )
    return (
        views
        + f" · {shortcut_label('analyze.previous')} / "
        + f"{shortcut_label('analyze.next')} previous/next · i capture"
    )
