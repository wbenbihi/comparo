"""Scrub real secret values from anything that leaves the process.

The DISPLAY sink masks secrets in the TUI, but drift and assertion *details* are
built from real EXECUTE-sink responses — and a server can echo a secret straight
back into a body it drifts on. So reports, exports, and the saved ``.reports/``
archive must be scrubbed against the real secret values before they are written.
This module is that single string-match backstop.
"""

import dataclasses
from collections.abc import Callable

from comparo.core.loader import LoadedProject
from comparo.core.models import Environment
from comparo.core.secrets import ExecuteSecrets
from comparo.core.secrets import SecretError

#: What a redacted secret becomes — the same glyph the DISPLAY sink uses.
MASK = "••••••"


def secret_values(project: LoadedProject) -> set[str]:
    """Every environment's resolved secret values (a superset masked everywhere)."""
    values: set[str] = set()
    for obj in project.objects.values():
        if not isinstance(obj, Environment):
            continue
        sources = obj.spec.secrets or {}
        secrets = ExecuteSecrets(dict(sources), project.root)
        for name in sources:
            try:
                value = secrets[name]
            except SecretError:
                continue
            if value:
                values.add(value)
    return values


@dataclasses.dataclass(frozen=True, slots=True)
class Redactor:
    """Masks any known secret value found in a string."""

    values: tuple[str, ...]

    @classmethod
    def for_project(cls, project: LoadedProject) -> "Redactor":
        """Build a redactor over every resolved secret value in *project*."""
        # Longest-first, so a secret that contains a shorter one is masked whole.
        return cls(tuple(sorted(secret_values(project), key=len, reverse=True)))

    def text(self, text: str) -> str:
        """Return *text* with every known secret value replaced by the mask."""
        for value in self.values:
            if value in text:
                text = text.replace(value, MASK)
        return text


#: A redact callable — a ``Redactor.text`` or the identity when nothing to mask.
Redact = Callable[[str], str]


def identity(text: str) -> str:
    """The no-op redactor used when a caller has no secrets to mask."""
    return text
