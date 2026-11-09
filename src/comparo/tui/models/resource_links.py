"""Value-free resource links over complete logical identities."""

import json
from dataclasses import dataclass

from comparo.application import ResourceCoordinate
from comparo.application.project_outline import ResourceIdentityCoordinate
from comparo.tui.models.explorer import ExplorerCatalog
from comparo.tui.models.explorer import ResourceEntry


def logical_identity(coordinate: ResourceCoordinate) -> ResourceIdentityCoordinate:
    """Drop only the revision when retaining a navigation destination."""
    return ResourceIdentityCoordinate(
        namespace=coordinate.namespace,
        kind=coordinate.kind,
        resource_id=coordinate.resource_id,
    )


def linked_entry(
    catalog: ExplorerCatalog | None, target: ResourceIdentityCoordinate
) -> ResourceEntry | None:
    """Bind a unique logical target to the current catalog revision."""
    if catalog is None:
        return None
    matches = tuple(
        entry
        for entry in catalog.entries.values()
        if logical_identity(entry.summary.coordinate) == target
    )
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class ResourceDependency:
    """One directed, navigable edge with no cached resource values."""

    direction: str
    path: tuple[str, ...]
    target: ResourceIdentityCoordinate
    origin: str

    @property
    def target_kind(self) -> str:
        """Return the kind for existing presentation consumers."""
        return self.target.kind

    @property
    def target_id(self) -> str:
        """Return the resource ID for existing presentation consumers."""
        return self.target.resource_id

    @property
    def key(self) -> str:
        """Keep selection stable when unrelated graph rows change order."""
        return json.dumps(
            (
                self.direction,
                self.target.namespace,
                self.target.kind,
                self.target.resource_id,
                self.path,
                self.origin,
            )
        )
