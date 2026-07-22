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
    ENV = "env"
    #: A large value replaced by a hash+size marker in the display sink only. The
    #: real value is resolved and sent whole in the execute sink; the marker is a
    #: rendering artifact, never persisted as if it were the value.
    ELIDED = "elided"

    @property
    def tainted(self) -> bool:
        """Whether values of this origin must be masked and never persisted.

        Only a declared secret is tainted. ``$env``/``$file`` resolve real values
        that are masked *iff* they are a declared secret — that is the redactor's
        value-keyed floor, not this origin — so they are not tainted here. Masking
        is keyed off the ``secrets:`` declaration, never off the directive.
        """
        return self is Origin.SECRET


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
