"""Diagnostics produced while loading a comparo project."""

import dataclasses
from pathlib import Path


@dataclasses.dataclass(frozen=True, slots=True)
class Diagnostic:
    """A single problem found while loading, tied to a file and maybe a line."""

    file: Path
    message: str
    line: int | None = None
    hint: str | None = None

    def render(self, root: Path) -> str:
        """Format as ``path:line: message`` with an optional indented hint.

        Args:
            root: The project root, used to shorten the file path when possible.

        Returns:
            A human-readable, single- or two-line rendering of the diagnostic.
        """
        try:
            location = str(self.file.relative_to(root))
        except ValueError:
            location = str(self.file)
        if self.line is not None:
            location = f"{location}:{self.line}"
        rendered = f"{location}: {self.message}"
        if self.hint is not None:
            rendered = f"{rendered}\n  {self.hint}"
        return rendered


class LoadError(Exception):
    """Raised when a project fails to load; carries every diagnostic found."""

    def __init__(self, diagnostics: list[Diagnostic], root: Path) -> None:
        """Store the diagnostics and the project root they were found under.

        Args:
            diagnostics: Every problem found while loading the project.
            root: The project directory that failed to load.
        """
        self.diagnostics = diagnostics
        self.root = root
        super().__init__(f"{len(diagnostics)} problem(s) loading project at {root}")
