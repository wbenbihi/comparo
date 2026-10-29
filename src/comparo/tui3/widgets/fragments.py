"""Rich fragment builders — the one place text gets styled in tui3."""

from __future__ import annotations

from rich.text import Text

from comparo.tui3.theme import style

KIND_GLYPH: dict[str, tuple[str, str]] = {
    "env": ("●", "k.env"),
    "req": ("◆", "k.req"),
    "val": ("◈", "k.val"),
    "mat": ("▦", "k.mat"),
    "sch": ("⬡", "k.sch"),
    "diff": ("±", "k.diff"),
    "assert": ("⊨", "k.assert"),
    "exec": ("▶", "k.exec"),
}


def t(text: str, tok: str = "fg") -> Text:
    """One styled fragment."""
    return Text(text, style=style(tok))


def join(*parts: Text) -> Text:
    """Concatenate fragments."""
    out = Text()
    for part in parts:
        out.append_text(part)
    return out


def kind(kd: str, *, bold: bool = False) -> Text:
    """Entity glyph in its kind color."""
    glyph, token = KIND_GLYPH[kd]
    return Text(glyph, style=style(token.replace("k.", "k+") if bold else token))


def entity(kd: str, name: str, *, bold: bool = False) -> Text:
    """Glyph + name."""
    return join(kind(kd, bold=bold), t(" "), t(name, "b" if bold else "hi"))


def method(m: str) -> Text:
    """Method fragment — GET accent, writes amber (the v0 grammar)."""
    return t(m, "method.post" if m in ("POST", "PUT", "PATCH", "DELETE") else "method.get")


def pill(text: str, token: str) -> Text:
    """Bracketed mini-pill (default / live / replay / badges)."""
    return t(f"⟨{text}⟩", token)


def mark(on: bool) -> Text:
    """◉ / ○ selection mark."""
    return t("◉" if on else "○", "acc" if on else "dim")


def chip(label: str, value: str, tok: str = "b") -> Text:
    """Stat chip: dim label + emphasised value, boxed by spacing."""
    return join(t("⟨", "dim"), t(f"{label} ", "dim"), t(value, tok), t("⟩", "dim"))


def sep(txt: str = " · ") -> Text:
    """Dim separator."""
    return t(txt, "dim")


def dline(kd: str, *parts: Text) -> Text:
    """One git-diff line: kd in add|del|hunk|ctx."""
    lead = {"add": "+ ", "del": "- ", "hunk": "@ ", "ctx": "  "}[kd]
    out = Text(lead, style=style(f"diff.{kd}"))
    for part in parts:
        out.append_text(part)
    return out


def progress(pct: int, width: int = 40) -> Text:
    """A thin progress bar."""
    filled = round(width * pct / 100)
    return join(t("▰" * filled, "acc"), t("▱" * (width - filled), "dim"))
