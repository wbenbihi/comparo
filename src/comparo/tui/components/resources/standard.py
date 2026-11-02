"""Forward-compatible resource detail fallback."""

from .base import BaseKindResourceDetails


class GenericResourceDetails(BaseKindResourceDetails):
    """Forward-compatible fallback for unknown and plugin-defined kinds."""
