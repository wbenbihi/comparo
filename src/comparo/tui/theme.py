"""The comparo-ink Textual theme — meaning-named tokens as one cohesive palette.

Registering a real :class:`Theme` (rather than hand-painting hex) lets every
widget — the tree cursor, scrollbars, the footer, panel borders — derive from
the same tokens, which is what makes a Textual app read as designed.
"""

from textual.theme import Theme

COMPARO_INK = Theme(
    name="comparo-ink",
    primary="#6d9eff",  # accent — focus, selection, keys
    secondary="#a98bf0",  # axis — the matrix dimension
    accent="#6d9eff",
    foreground="#c5d0de",
    background="#0d1017",  # deep ink, not black
    surface="#131822",  # panels
    panel="#182030",  # focused surface
    success="#48a97f",  # same
    warning="#d99b3f",  # noise / degraded
    error="#e0566b",  # drift
    dark=True,
    variables={
        "border": "#28313f",
        "border-blurred": "#222b38",
        "block-cursor-background": "#213052",  # accent-tinted, not gray
        "block-cursor-foreground": "#eaf0f8",
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": "#1a2233",
        "block-hover-background": "#182030",
        "footer-background": "#131822",
        "footer-foreground": "#5c6878",
        "footer-key-foreground": "#6d9eff",
        "footer-key-background": "#182030",
        "footer-description-foreground": "#7f8ba0",
        "scrollbar": "#131822",
        "scrollbar-hover": "#28313f",
        "scrollbar-active": "#6d9eff",
        "scrollbar-background": "#131822",
        "scrollbar-background-hover": "#131822",
        "scrollbar-background-active": "#131822",
    },
)
