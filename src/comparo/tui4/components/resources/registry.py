"""Explicit resource-kind to widget registry."""

from comparo.tui4.models.resource import ResourceDetail

from .base import BaseKindResourceDetails
from .matrix import MatrixResourceDetails
from .standard import EnvironmentResourceDetails
from .standard import GenericResourceDetails
from .standard import ProfileResourceDetails
from .standard import ProjectResourceDetails
from .standard import RequestResourceDetails
from .standard import ScenarioResourceDetails
from .standard import SchemaResourceDetails
from .standard import ValueResourceDetails

_REGISTERED_WIDGETS: tuple[type[BaseKindResourceDetails], ...] = (
    ProjectResourceDetails,
    EnvironmentResourceDetails,
    RequestResourceDetails,
    MatrixResourceDetails,
    SchemaResourceDetails,
    ValueResourceDetails,
    ProfileResourceDetails,
    ScenarioResourceDetails,
)


def _build_registry() -> dict[str, type[BaseKindResourceDetails]]:
    """Build and validate the explicit kind registry once at import time."""
    registry: dict[str, type[BaseKindResourceDetails]] = {}
    for widget in _REGISTERED_WIDGETS:
        for kind in widget.KINDS:
            if kind in registry:
                raise RuntimeError(f"duplicate resource widget registration: {kind}")
            registry[kind] = widget
    return registry


RESOURCE_WIDGETS = _build_registry()


def resource_widget(detail: ResourceDetail) -> BaseKindResourceDetails:
    """Construct the registered widget or the forward-compatible fallback."""
    kind = detail.entry.summary.coordinate.kind
    widget = RESOURCE_WIDGETS.get(kind, GenericResourceDetails)
    return widget(detail)
