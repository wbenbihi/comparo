"""Explicit built-in trusted-local authority for deliberate local operation."""

from __future__ import annotations

from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import SourceLocation
from comparo.core.policy.compiler import default_planning_ceilings
from comparo.core.policy.compiler import policy_fingerprint
from comparo.core.policy.models import POLICY_API_VERSION
from comparo.core.policy.models import AmbientEnvironmentPolicy
from comparo.core.policy.models import CompiledExecutionPolicy
from comparo.core.policy.models import NetworkAddressClass
from comparo.core.policy.models import PolicyArtifacts
from comparo.core.policy.models import PolicyHttp
from comparo.core.policy.models import PolicyPlanning
from comparo.core.policy.models import PolicyValues
from comparo.core.policy.models import RemoteSchemaPolicy

_IDENTITY = ResourceIdentity(
    namespace=NamespaceId(value="operator"),
    kind=ResourceKind.EXECUTION_POLICY,
    resource_id=ResourceId(value="trusted-local"),
)
_METADATA = Metadata(
    name="Comparo trusted local policy",
    description="Explicit built-in authority for deliberate local execution.",
    tags=("trusted-local",),
)
_LOCATION = SourceLocation(
    source_id=SourceId(value="trusted-local-policy"),
    document=PositiveCount(value=1),
    line=PositiveCount(value=1),
    column=PositiveCount(value=1),
)


def trusted_local_policy() -> CompiledExecutionPolicy:
    """Return an explicit permissive local policy with finite planning ceilings."""
    planning = PolicyPlanning(
        subjects=(),
        environments=(),
        allow_any_subjects=True,
        allow_any_environments=True,
        ceilings=default_planning_ceilings(),
    )
    http = PolicyHttp(
        allowed_schemes=tuple(sorted(HttpScheme, key=lambda item: item.value)),
        allowed_methods=tuple(
            HttpMethod(value=value)
            for value in sorted(
                {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
            )
        ),
        allowed_hosts=(),
        allowed_ports=(),
        allowed_address_classes=tuple(sorted(NetworkAddressClass, key=lambda item: item.value)),
        allow_any_host=True,
        allow_any_port=True,
        require_mutation_approval=False,
        allow_proxy_dns=True,
    )
    values = PolicyValues(
        ambient_environment=AmbientEnvironmentPolicy(names=(), allow_any=True),
        allow_project_files=True,
        remote_schemas=RemoteSchemaPolicy(sources=(), allow_any=True),
    )
    artifacts = PolicyArtifacts(
        allow_sanitized_persistence=True,
        roots=("/",),
    )
    fingerprint = policy_fingerprint(
        _IDENTITY,
        _METADATA,
        planning,
        http,
        values,
        artifacts,
    )
    return CompiledExecutionPolicy(
        api_version=POLICY_API_VERSION,
        definition=DefinitionRef(identity=_IDENTITY, fingerprint=fingerprint),
        metadata=_METADATA,
        location=_LOCATION,
        planning=planning,
        http=http,
        values=values,
        artifacts=artifacts,
    )


__all__ = ("trusted_local_policy",)
