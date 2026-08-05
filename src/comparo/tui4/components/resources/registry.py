"""Explicit resource-kind to widget registry."""

from comparo.tui4.models.resource import ResourceDetail
from comparo.tui4.models.resource import ResourcePresentation

from .base import BaseKindResourceDetails
from .data_profiles import ArtifactProfileResourceDetails
from .data_profiles import RedactionProfileResourceDetails
from .data_profiles import SafetyProfileResourceDetails
from .environment import EnvironmentResourceDetails
from .evaluation_profiles import AssertionProfileResourceDetails
from .evaluation_profiles import DiffProfileResourceDetails
from .evaluation_profiles import GateProfileResourceDetails
from .execution_policy import ExecutionPolicyResourceDetails
from .execution_profile import ExecutionProfileResourceDetails
from .matrix import MatrixResourceDetails
from .project import ProjectResourceDetails
from .request import RequestResourceDetails
from .scenario import ScenarioResourceDetails
from .schema import SchemaResourceDetails
from .standard import GenericResourceDetails
from .value import ValueResourceDetails

_REGISTERED_WIDGETS: tuple[type[BaseKindResourceDetails], ...] = (
    ProjectResourceDetails,
    EnvironmentResourceDetails,
    RequestResourceDetails,
    ScenarioResourceDetails,
    MatrixResourceDetails,
    SchemaResourceDetails,
    ValueResourceDetails,
    ExecutionProfileResourceDetails,
    AssertionProfileResourceDetails,
    DiffProfileResourceDetails,
    GateProfileResourceDetails,
    SafetyProfileResourceDetails,
    RedactionProfileResourceDetails,
    ArtifactProfileResourceDetails,
    ExecutionPolicyResourceDetails,
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


def resource_widget(
    detail: ResourceDetail,
    *,
    section: str | None = None,
) -> BaseKindResourceDetails:
    """Construct the registered widget or the forward-compatible fallback."""
    kind = detail.entry.summary.coordinate.kind
    widget = RESOURCE_WIDGETS.get(kind, GenericResourceDetails)
    return widget(detail, section=section)


def resource_presentation(kind: str) -> ResourcePresentation:
    """Return the kind-owned host policy or the generic fallback policy."""
    widget = RESOURCE_WIDGETS.get(kind, GenericResourceDetails)
    return widget.PRESENTATION
