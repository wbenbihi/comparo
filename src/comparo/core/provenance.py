"""Provenance and taint for resolved configuration values.

Every value produced by the resolver remembers where it came from. That single
fact drives three features: masking secrets in the display sink, scrubbing them
from snapshots, and explaining a diff ("these differ *because* …").
"""

import dataclasses
import enum


class Origin(enum.Enum):
    """Where a resolved value came from."""

    LITERAL = "literal"
    VARIABLE = "variable"
    SECRET = "secret"
    INSTANCE = "instance"
    MATRIX = "matrix"
    FILE = "file"

    @property
    def tainted(self) -> bool:
        """Whether values of this origin must be masked and never persisted."""
        return self in (Origin.SECRET, Origin.FILE)


@dataclasses.dataclass(frozen=True, slots=True)
class Trail:
    """One non-literal value in a resolved tree, for the provenance display."""

    path: str
    origin: Origin
    detail: str

    @property
    def tainted(self) -> bool:
        """Whether the value at this path must be masked."""
        return self.origin.tainted
