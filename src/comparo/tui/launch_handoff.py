"""Safe resource-identity handoff from Explore into an unexecuted Launch target."""

from textual.message import Message

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.tui.components.launchconfig import LaunchConfiguration
from comparo.tui.models.explorer import ExplorerCatalog


def apply_launch_target(
    configuration: LaunchConfiguration,
    catalog: ExplorerCatalog,
    resource: ResourceCoordinate,
    environment: ResourceCoordinate | None,
) -> None:
    """Validate the resource revision before replacing a draft with explicit selections."""
    validate_launch_target(catalog, resource, environment)
    configuration.load(catalog)
    if resource.kind == "ExecutionProfile":
        configuration.choices("mode").selected = ("execution-profile",)
        configuration.choices("profile-source").selected = ("saved",)
        configuration.choices("profile").selected = (resource.resource_id,)
    else:
        configuration.choices("requests").selected = (resource.resource_id,)
        if environment is not None:
            configuration.choices("mode").selected = ("assertion",)
            configuration.choices("environments").selected = (environment.resource_id,)
    configuration._reflect()
    configuration.post_message(LaunchConfiguration.Changed(configuration, configuration.draft()))


def validate_launch_target(
    catalog: ExplorerCatalog, resource: ResourceCoordinate, environment: ResourceCoordinate | None
) -> None:
    """Validate identity before invalidating any existing prepared plan."""
    if not any(entry.summary.coordinate == resource for entry in catalog.entries.values()):
        raise ValueError("The resource changed. Reload Explore before using it in Launch.")
    if resource.kind not in {"Request", "ExecutionProfile"}:
        raise ValueError("Select a Request or ExecutionProfile.")
    if environment is not None and not any(
        entry.summary.coordinate == environment for entry in catalog.entries.values()
    ):
        raise ValueError("The execution context changed. Select an Environment again.")


class UseInLaunch(Message):
    """Explicit user intent carrying only safe resource identity and context."""

    def __init__(
        self, resource: ResourceCoordinate, environment: ResourceCoordinate | None
    ) -> None:
        """Freeze selected revisions at activation time."""
        super().__init__()
        self.resource = resource
        self.environment = environment
