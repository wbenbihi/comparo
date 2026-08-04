"""Standard field-based resource detail widgets."""

from typing import ClassVar
from typing import override

from comparo.tui4.components.list import Field
from comparo.tui4.models.explorer import PROFILE_KINDS
from comparo.tui4.models.resource_presenters import environment_fields
from comparo.tui4.models.resource_presenters import generic_fields
from comparo.tui4.models.resource_presenters import profile_fields
from comparo.tui4.models.resource_presenters import request_fields
from comparo.tui4.models.resource_presenters import schema_fields
from comparo.tui4.models.resource_presenters import value_fields

from .base import BaseKindResourceDetails


class EnvironmentResourceDetails(BaseKindResourceDetails):
    """Environment role, locality, and safety configuration."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Environment"})

    @property
    @override
    def fields(self) -> tuple[Field, ...]:
        """Return Environment-specific overview rows."""
        return environment_fields(self.entry)


class RequestResourceDetails(BaseKindResourceDetails):
    """Request method and structural dependencies."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Request"})

    @property
    @override
    def fields(self) -> tuple[Field, ...]:
        """Return Request-specific overview rows."""
        return request_fields(self.entry)


class SchemaResourceDetails(BaseKindResourceDetails):
    """Schema shape and consumers."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Schema"})

    @property
    @override
    def fields(self) -> tuple[Field, ...]:
        """Return Schema-specific overview rows."""
        return schema_fields(self.entry)


class ValueResourceDetails(BaseKindResourceDetails):
    """Reusable Value shape and consumers without expression resolution."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Value"})

    @property
    @override
    def fields(self) -> tuple[Field, ...]:
        """Return Value-specific overview rows."""
        return value_fields(self.entry)


class ProfileResourceDetails(BaseKindResourceDetails):
    """Shared structural presentation for policy and profile resources."""

    KINDS: ClassVar[frozenset[str]] = PROFILE_KINDS | frozenset({"ExecutionPolicy"})

    @property
    @override
    def fields(self) -> tuple[Field, ...]:
        """Return safe profile dependency rows."""
        return profile_fields(self.entry)


class ScenarioResourceDetails(BaseKindResourceDetails):
    """Future Scenario structure without implying current executability."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Scenario"})

    @property
    @override
    def fields(self) -> tuple[Field, ...]:
        """Return the available structural Scenario summary."""
        return generic_fields(self.entry)


class GenericResourceDetails(BaseKindResourceDetails):
    """Forward-compatible fallback for unknown and plugin-defined kinds."""
