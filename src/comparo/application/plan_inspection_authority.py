"""Typed HTTP, value-source, and artifact authority facts for Plan review."""

from __future__ import annotations

from typing import final

import msgspec


class PlanHttpAuthorityInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Safe compiled HTTP authority relevant before execution."""

    allowed_schemes: tuple[str, ...]
    allowed_methods: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    allowed_ports: tuple[int, ...]
    allow_any_host: bool
    allow_any_port: bool
    require_mutation_approval: bool
    allowed_address_classes: tuple[str, ...] = ()
    allow_proxy_dns: bool = False

    def __post_init__(self) -> None:
        """Reject contradictory or untyped HTTP authority summaries."""
        text_lists = (
            self.allowed_schemes,
            self.allowed_methods,
            self.allowed_hosts,
            self.allowed_address_classes,
        )
        if any(
            type(values) is not tuple
            or any(type(item) is not str or not item for item in values)
            or len(values) != len(set(values))
            for values in text_lists
        ):
            raise ValueError("plan HTTP authority lists must be unique typed text tuples")
        if type(self.allowed_ports) is not tuple or any(
            type(item) is not int or item < 1 or item > 65_535 for item in self.allowed_ports
        ):
            raise ValueError("plan HTTP authority ports must be valid exact integers")
        if len(self.allowed_ports) != len(set(self.allowed_ports)):
            raise ValueError("plan HTTP authority ports must be unique")
        flags = (
            self.allow_any_host,
            self.allow_any_port,
            self.require_mutation_approval,
            self.allow_proxy_dns,
        )
        if any(type(item) is not bool for item in flags):
            raise TypeError("plan HTTP authority flags must be exact booleans")
        if not self.allowed_schemes or not self.allowed_methods:
            raise ValueError("plan HTTP authority requires schemes and methods")
        if self.allow_any_host and self.allowed_hosts:
            raise ValueError("plan HTTP host wildcard cannot retain an allowlist")
        if self.allow_any_port and self.allowed_ports:
            raise ValueError("plan HTTP port wildcard cannot retain an allowlist")
        if not self.allow_any_port and not self.allowed_ports:
            raise ValueError("plan HTTP authority requires explicit ports or a wildcard")


@final
class PlanSecretEgressSourceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One named sensitive source identity authorized for destination-bound egress."""

    kind: str
    name: str

    def __post_init__(self) -> None:
        """Require a closed source kind and a safe non-empty logical name."""
        if self.kind not in {"environment", "secret"}:
            raise ValueError("plan secret egress source kind is unsupported")
        if type(self.name) is not str or not self.name:
            raise ValueError("plan secret egress source name must be non-empty text")


@final
class PlanSecretEgressRuleInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free secret source and destination authority rule."""

    sources: tuple[PlanSecretEgressSourceInspection, ...]
    allowed_schemes: tuple[str, ...]
    allowed_methods: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    allowed_ports: tuple[int, ...]

    def __post_init__(self) -> None:
        """Require complete unique source and destination authority facts."""
        if type(self.sources) is not tuple or any(
            type(item) is not PlanSecretEgressSourceInspection for item in self.sources
        ):
            raise TypeError("plan secret egress sources must be an immutable typed tuple")
        if not self.sources or len(self.sources) != len(set(self.sources)):
            raise ValueError("plan secret egress sources must be non-empty and unique")
        for values in (self.allowed_schemes, self.allowed_methods, self.allowed_hosts):
            if type(values) is not tuple or any(
                type(item) is not str or not item for item in values
            ):
                raise ValueError("plan secret egress destinations must be typed text tuples")
            if not values or len(values) != len(set(values)):
                raise ValueError("plan secret egress destinations must be non-empty and unique")
        if type(self.allowed_ports) is not tuple or any(
            type(item) is not int or not 1 <= item <= 65_535 for item in self.allowed_ports
        ):
            raise ValueError("plan secret egress ports must be valid exact integers")
        if not self.allowed_ports or len(self.allowed_ports) != len(set(self.allowed_ports)):
            raise ValueError("plan secret egress ports must be non-empty and unique")


@final
class PlanValueAuthorityInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Safe external value-source and secret-egress authority."""

    ambient_environment_names: tuple[str, ...]
    allow_any_ambient_environment: bool
    allow_project_files: bool
    remote_schema_sources: tuple[str, ...]
    allow_any_remote_schemas: bool
    secret_egress: tuple[PlanSecretEgressRuleInspection, ...]
    allow_any_secret_egress: bool

    def __post_init__(self) -> None:
        """Require exact safe authority facts and coherent non-negative rule counts."""
        for values in (self.ambient_environment_names, self.remote_schema_sources):
            if type(values) is not tuple or any(
                type(item) is not str or not item for item in values
            ):
                raise ValueError("plan value authority names must be typed non-empty text")
            if len(values) != len(set(values)):
                raise ValueError("plan value authority names must be unique")
        flags = (
            self.allow_any_ambient_environment,
            self.allow_project_files,
            self.allow_any_remote_schemas,
            self.allow_any_secret_egress,
        )
        if any(type(item) is not bool for item in flags):
            raise TypeError("plan value authority flags must be exact booleans")
        if type(self.secret_egress) is not tuple or any(
            type(item) is not PlanSecretEgressRuleInspection for item in self.secret_egress
        ):
            raise TypeError("plan secret egress authority must be an immutable typed tuple")
        if len(self.secret_egress) != len(set(self.secret_egress)):
            raise ValueError("plan secret egress authority rules must be unique")
        if self.allow_any_ambient_environment and self.ambient_environment_names:
            raise ValueError("ambient Environment wildcard cannot retain names")
        if self.allow_any_remote_schemas and self.remote_schema_sources:
            raise ValueError("remote Schema wildcard cannot retain sources")
        if self.allow_any_secret_egress and self.secret_egress:
            raise ValueError("secret egress wildcard cannot retain rules")


@final
class PlanArtifactAuthorityInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Safe sanitized artifact-persistence authority without filesystem paths."""

    allow_sanitized_persistence: bool
    root_count: int
    selected_root_present: bool
    selected_root_authorized: bool

    def __post_init__(self) -> None:
        """Require a coherent permission and root-count summary."""
        if type(self.allow_sanitized_persistence) is not bool:
            raise TypeError("plan artifact authority permission must be a bool")
        if type(self.root_count) is not int or self.root_count < 0:
            raise ValueError("plan artifact root count must be non-negative")
        if self.allow_sanitized_persistence != (self.root_count > 0):
            raise ValueError("plan artifact persistence must match the admitted root count")
        if any(
            type(item) is not bool
            for item in (self.selected_root_present, self.selected_root_authorized)
        ):
            raise TypeError("selected artifact root authority must use booleans")
        if self.selected_root_authorized and not self.selected_root_present:
            raise ValueError("an absent artifact root cannot be authorized")
        if self.selected_root_authorized and not self.allow_sanitized_persistence:
            raise ValueError("artifact root authorization requires persistence authority")


__all__ = (
    "PlanArtifactAuthorityInspection",
    "PlanHttpAuthorityInspection",
    "PlanSecretEgressRuleInspection",
    "PlanSecretEgressSourceInspection",
    "PlanValueAuthorityInspection",
)
