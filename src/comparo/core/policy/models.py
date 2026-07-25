"""Immutable products of the independently trusted ExecutionPolicy frontend."""

from __future__ import annotations

import re
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import is_http_host
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.provenance import SourceLocation

POLICY_API_VERSION = "comparo.policy/v1alpha1"

PLANNING_RESOURCES: tuple[BudgetResource, ...] = (
    BudgetResource.REQUESTS,
    BudgetResource.MATRIX_CELLS,
    BudgetResource.TRIALS,
    BudgetResource.ENVIRONMENT_EXECUTIONS,
    BudgetResource.ATTEMPTS,
    BudgetResource.COMPARISONS,
    BudgetResource.QUEUED_WORK,
    BudgetResource.CONCURRENCY,
)

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WINDOWS_ABSOLUTE = re.compile(r"[A-Z]:/")


class NetworkAddressClass(DomainStrEnum):
    """Closed network-address categories available to HTTP authorization."""

    PUBLIC = "public"
    PRIVATE = "private"
    LOOPBACK = "loopback"
    LINK_LOCAL = "link-local"
    MULTICAST = "multicast"
    UNSPECIFIED = "unspecified"
    RESERVED = "reserved"


@final
class PolicyResourceSelector(msgspec.Struct, frozen=True, kw_only=True):
    """Namespace-independent resource allowlist selector bound at Project invocation time."""

    kind: ResourceKind
    resource_id: ResourceId

    def __post_init__(self) -> None:
        """Accept only subjects and Environments controlled during planning."""
        if type(self.kind) is not ResourceKind:
            raise TypeError("policy resource selector kind must be typed")
        if self.kind not in {
            ResourceKind.REQUEST,
            ResourceKind.SCENARIO,
            ResourceKind.ENVIRONMENT,
        }:
            raise ValueError("policy resource selector kind is not planning-selectable")
        if type(self.resource_id) is not ResourceId:
            raise TypeError("policy resource selector ID must be a ResourceId")


@final
class PolicyPlanning(msgspec.Struct, frozen=True, kw_only=True):
    """Complete planning authority requests with finite version-owned ceilings."""

    subjects: tuple[PolicyResourceSelector, ...]
    environments: tuple[PolicyResourceSelector, ...]
    allow_any_subjects: bool
    allow_any_environments: bool
    ceilings: tuple[PlanningBudgetValue, ...]

    def __post_init__(self) -> None:
        """Require canonical allowlists and every planning ceiling exactly once."""
        _validate_selectors(self.subjects, subject=True)
        _validate_selectors(self.environments, subject=False)
        if (
            type(self.allow_any_subjects) is not bool
            or type(self.allow_any_environments) is not bool
        ):
            raise TypeError("policy planning wildcard flags must be bool values")
        if self.allow_any_subjects and self.subjects:
            raise ValueError("allowAnySubjects cannot retain a redundant subject allowlist")
        if self.allow_any_environments and self.environments:
            raise ValueError("allowAnyEnvironments cannot retain a redundant environment allowlist")
        if type(self.ceilings) is not tuple or any(
            type(item) is not PlanningBudgetValue for item in self.ceilings
        ):
            raise TypeError("policy planning ceilings must be immutable and typed")
        resources = tuple(item.resource for item in self.ceilings)
        if resources != PLANNING_RESOURCES:
            raise ValueError("policy planning ceilings must cover every resource canonically")


@final
class PolicyHttp(msgspec.Struct, frozen=True, kw_only=True):
    """Explicit HTTP destinations and safeguards granted by an ExecutionPolicy."""

    allowed_schemes: tuple[HttpScheme, ...]
    allowed_methods: tuple[HttpMethod, ...]
    allowed_hosts: tuple[HttpHost, ...]
    allowed_ports: tuple[HttpPort, ...]
    allowed_address_classes: tuple[NetworkAddressClass, ...]
    allow_any_host: bool
    allow_any_port: bool
    require_mutation_approval: bool
    allow_proxy_dns: bool

    def __post_init__(self) -> None:
        """Require canonical typed allowlists and explicit authority flags."""
        _validate_enum_values(self.allowed_schemes, HttpScheme, "HTTP schemes")
        _validate_methods(self.allowed_methods)
        _validate_hosts(self.allowed_hosts)
        _validate_ports(self.allowed_ports)
        _validate_enum_values(
            self.allowed_address_classes,
            NetworkAddressClass,
            "address classes",
        )
        for value in (
            self.allow_any_host,
            self.allow_any_port,
            self.require_mutation_approval,
            self.allow_proxy_dns,
        ):
            if type(value) is not bool:
                raise TypeError("policy HTTP authority flags must be bool values")
        if self.allow_any_host and self.allowed_hosts:
            raise ValueError("allowAnyHost cannot retain a redundant host allowlist")
        if self.allow_any_port and self.allowed_ports:
            raise ValueError("allowAnyPort cannot retain a redundant port allowlist")
        if not self.allow_any_port and not self.allowed_ports:
            raise ValueError("HTTP policy requires allowedPorts unless allowAnyPort is true")


@final
class AmbientEnvironmentPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Names from the run-start ambient environment that may be snapshotted."""

    names: tuple[str, ...]
    allow_any: bool

    def __post_init__(self) -> None:
        """Require canonical environment names without an ambiguous wildcard plus list."""
        if type(self.names) is not tuple or any(
            type(name) is not str or _ENVIRONMENT_NAME.fullmatch(name) is None or len(name) > 255
            for name in self.names
        ):
            raise ValueError("ambient environment names must use portable variable syntax")
        if self.names != tuple(sorted(set(self.names))):
            raise ValueError("ambient environment names must be unique and canonical")
        if type(self.allow_any) is not bool:
            raise TypeError("ambient environment allowAny must be bool")
        if self.allow_any and self.names:
            raise ValueError("ambient environment allowAny cannot retain a redundant allowlist")


@final
class RemoteSchemaPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Exact public schema sources that may be fetched outside Project configuration."""

    sources: tuple[NamedCapabilityTarget, ...]
    allow_any: bool

    def __post_init__(self) -> None:
        """Require canonical schema-source targets without wildcard ambiguity."""
        if type(self.sources) is not tuple or any(
            type(source) is not NamedCapabilityTarget
            or source.kind is not CapabilityTargetKind.SCHEMA_SOURCE
            or not source.identifier.startswith(("https://", "http://"))
            for source in self.sources
        ):
            raise TypeError("remote schema sources must be exact HTTP(S) schema targets")
        if self.sources != tuple(sorted(set(self.sources), key=lambda item: item.identifier)):
            raise ValueError("remote schema sources must be unique and canonical")
        if type(self.allow_any) is not bool:
            raise TypeError("remote schema allowAny must be bool")
        if self.allow_any and self.sources:
            raise ValueError("remote schema allowAny cannot retain a redundant allowlist")


@final
class PolicyValues(msgspec.Struct, frozen=True, kw_only=True):
    """External value-source authority independent from namespace precedence."""

    ambient_environment: AmbientEnvironmentPolicy
    allow_project_files: bool
    remote_schemas: RemoteSchemaPolicy

    def __post_init__(self) -> None:
        """Require explicit typed value-source policy sections."""
        if type(self.ambient_environment) is not AmbientEnvironmentPolicy:
            raise TypeError("policy ambient environment section must be typed")
        if type(self.allow_project_files) is not bool:
            raise TypeError("policy project-file permission must be bool")
        if type(self.remote_schemas) is not RemoteSchemaPolicy:
            raise TypeError("policy remote schema section must be typed")


@final
class PolicyArtifacts(msgspec.Struct, frozen=True, kw_only=True):
    """Sanitized-only persistence authority and exact operator-owned filesystem roots."""

    allow_sanitized_persistence: bool
    roots: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require absolute safe root text and coherent persistence permission."""
        if type(self.allow_sanitized_persistence) is not bool:
            raise TypeError("sanitized persistence permission must be bool")
        if type(self.roots) is not tuple or any(not _is_absolute_root(root) for root in self.roots):
            raise ValueError("artifact roots must be bounded absolute paths")
        if self.roots != tuple(sorted(set(self.roots))):
            raise ValueError("artifact roots must be unique and canonical")
        if self.allow_sanitized_persistence != bool(self.roots):
            raise ValueError("sanitized persistence permission must match configured roots")


@final
class CompiledExecutionPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Complete directive-free operator authority with an exact definition revision."""

    api_version: str
    definition: DefinitionRef
    metadata: Metadata
    location: SourceLocation
    planning: PolicyPlanning
    http: PolicyHttp
    values: PolicyValues
    artifacts: PolicyArtifacts

    def __post_init__(self) -> None:
        """Require one coherent independently versioned ExecutionPolicy product."""
        if self.api_version != POLICY_API_VERSION:
            raise ValueError("compiled policy API version is unsupported")
        if type(self.definition) is not DefinitionRef:
            raise TypeError("compiled policy definition must be a DefinitionRef")
        if self.definition.identity.kind is not ResourceKind.EXECUTION_POLICY:
            raise ValueError("compiled policy definition must identify ExecutionPolicy")
        if type(self.metadata) is not Metadata or self.metadata.name is None:
            raise TypeError("compiled policy metadata must contain a name")
        if type(self.location) is not SourceLocation:
            raise TypeError("compiled policy location must be a SourceLocation")
        if type(self.planning) is not PolicyPlanning:
            raise TypeError("compiled policy planning section must be typed")
        if type(self.http) is not PolicyHttp:
            raise TypeError("compiled policy HTTP section must be typed")
        if type(self.values) is not PolicyValues:
            raise TypeError("compiled policy values section must be typed")
        if type(self.artifacts) is not PolicyArtifacts:
            raise TypeError("compiled policy artifact section must be typed")


@final
class PolicyCompilationResult(msgspec.Struct, frozen=True, kw_only=True):
    """Fail-closed policy compilation product with source-located safe diagnostics."""

    policy: CompiledExecutionPolicy | None = None
    diagnostics: tuple[ConfigurationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Return exactly one policy or at least one typed diagnostic."""
        if self.policy is not None and type(self.policy) is not CompiledExecutionPolicy:
            raise TypeError("policy compilation product must be CompiledExecutionPolicy")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ConfigurationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("policy diagnostics must be immutable and typed")
        if (self.policy is None) == bool(self.diagnostics):
            return
        raise ValueError("policy compilation must contain exactly a policy or diagnostics")


def planning_authority(
    policy: CompiledExecutionPolicy,
    *,
    project_namespace: NamespaceId,
) -> PlanningAuthority:
    """Bind namespace-independent policy selectors to one compiled Project namespace."""
    if type(policy) is not CompiledExecutionPolicy:
        raise TypeError("planning authority requires CompiledExecutionPolicy")
    if type(project_namespace) is not NamespaceId:
        raise TypeError("planning authority requires a Project NamespaceId")
    return PlanningAuthority(
        policy=policy.definition,
        allowed_subjects=(
            None
            if policy.planning.allow_any_subjects
            else _selector_identities(policy.planning.subjects, project_namespace)
        ),
        allowed_environments=(
            None
            if policy.planning.allow_any_environments
            else _selector_identities(policy.planning.environments, project_namespace)
        ),
        ceilings=policy.planning.ceilings,
    )


def is_policy_host(value: object) -> TypeGuard[HttpHost]:
    """Narrow a policy value to the shared closed HTTP host union."""
    return type(value) in {DnsHost, Ipv4Host, Ipv6Host}


def _selector_identities(
    selectors: tuple[PolicyResourceSelector, ...],
    namespace: NamespaceId,
) -> tuple[ResourceIdentity, ...]:
    return tuple(
        ResourceIdentity(namespace=namespace, kind=item.kind, resource_id=item.resource_id)
        for item in selectors
    )


def _validate_selectors(
    selectors: tuple[PolicyResourceSelector, ...],
    *,
    subject: bool,
) -> None:
    if type(selectors) is not tuple or any(
        type(item) is not PolicyResourceSelector for item in selectors
    ):
        raise TypeError("policy planning allowlists must be immutable and typed")
    allowed = (
        {ResourceKind.REQUEST, ResourceKind.SCENARIO} if subject else {ResourceKind.ENVIRONMENT}
    )
    if any(item.kind not in allowed for item in selectors):
        raise ValueError("policy planning allowlist contains an invalid resource kind")
    if selectors != tuple(sorted(set(selectors), key=_selector_sort_key)):
        raise ValueError("policy planning allowlists must be unique and canonical")


def _validate_enum_values[EnumT: DomainStrEnum](
    values: tuple[EnumT, ...],
    expected: type[EnumT],
    label: str,
) -> None:
    if type(values) is not tuple or any(type(item) is not expected for item in values):
        raise TypeError(f"policy {label} must be immutable and typed")
    if not values or values != tuple(sorted(set(values), key=lambda item: item.value)):
        raise ValueError(f"policy {label} must be non-empty, unique, and canonical")


def _validate_methods(values: tuple[HttpMethod, ...]) -> None:
    if type(values) is not tuple or any(type(item) is not HttpMethod for item in values):
        raise TypeError("policy HTTP methods must be immutable and typed")
    if not values or values != tuple(sorted(set(values), key=lambda item: item.value)):
        raise ValueError("policy HTTP methods must be non-empty, unique, and canonical")


def _validate_hosts(values: tuple[HttpHost, ...]) -> None:
    if type(values) is not tuple or any(not is_http_host(item) for item in values):
        raise TypeError("policy HTTP hosts must be immutable supported hosts")
    if values != tuple(sorted(set(values), key=_host_sort_key)):
        raise ValueError("policy HTTP hosts must be unique and canonical")


def _validate_ports(values: tuple[HttpPort, ...]) -> None:
    if type(values) is not tuple or any(type(item) is not HttpPort for item in values):
        raise TypeError("policy HTTP ports must be immutable and typed")
    if values != tuple(sorted(set(values), key=lambda item: item.value)):
        raise ValueError("policy HTTP ports must be unique and canonical")


def _selector_sort_key(item: PolicyResourceSelector) -> tuple[str, str]:
    return item.kind.value, item.resource_id.value


def _host_sort_key(host: HttpHost) -> tuple[str, str]:
    return type(host).__name__, host.value


def _is_absolute_root(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 4096:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if value == "/" or _WINDOWS_ABSOLUTE.fullmatch(value) is not None:
        return True
    if value.startswith("/"):
        remainder = value[1:]
    elif _WINDOWS_ABSOLUTE.match(value) is not None:
        remainder = value[3:]
    else:
        return False
    return not value.endswith("/") and all(
        segment not in {"", ".", ".."} for segment in remainder.split("/")
    )


__all__ = (
    "PLANNING_RESOURCES",
    "POLICY_API_VERSION",
    "AmbientEnvironmentPolicy",
    "CompiledExecutionPolicy",
    "NetworkAddressClass",
    "PolicyArtifacts",
    "PolicyCompilationResult",
    "PolicyHttp",
    "PolicyPlanning",
    "PolicyResourceSelector",
    "PolicyValues",
    "RemoteSchemaPolicy",
    "is_policy_host",
    "planning_authority",
)
