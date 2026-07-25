"""Direct-construction invariants for immutable ExecutionPolicy products."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import msgspec
import pytest

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.provenance import SourceLocation
from comparo.core.policy import AmbientEnvironmentPolicy
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import PolicyArtifacts
from comparo.core.policy import PolicyCompilationResult
from comparo.core.policy import PolicyPlanning
from comparo.core.policy import PolicyResourceSelector
from comparo.core.policy import PolicyValues
from comparo.core.policy import RemoteSchemaPolicy
from comparo.core.policy import trusted_local_policy


def _assert_rejected(
    error: type[Exception],
    match: str,
    callback: Callable[[], object],
) -> None:
    with pytest.raises(error, match=match):
        callback()


def test_selector_and_planning_reject_untyped_or_noncanonical_values() -> None:
    request = PolicyResourceSelector(
        kind=ResourceKind.REQUEST,
        resource_id=ResourceId(value="one"),
    )
    environment = PolicyResourceSelector(
        kind=ResourceKind.ENVIRONMENT,
        resource_id=ResourceId(value="prod"),
    )
    ceilings = trusted_local_policy().planning.ceilings
    cases: tuple[tuple[type[Exception], str, Callable[[], object]], ...] = (
        (
            TypeError,
            "kind must be typed",
            lambda: PolicyResourceSelector(
                kind=cast("ResourceKind", "Request"), resource_id=ResourceId(value="one")
            ),
        ),
        (
            TypeError,
            "ResourceId",
            lambda: PolicyResourceSelector(
                kind=ResourceKind.REQUEST, resource_id=cast("ResourceId", "one")
            ),
        ),
        (
            TypeError,
            "immutable and typed",
            lambda: PolicyPlanning(
                subjects=cast("tuple[PolicyResourceSelector, ...]", [request]),
                environments=(),
                allow_any_subjects=False,
                allow_any_environments=False,
                ceilings=ceilings,
            ),
        ),
        (
            ValueError,
            "invalid resource kind",
            lambda: PolicyPlanning(
                subjects=(environment,),
                environments=(),
                allow_any_subjects=False,
                allow_any_environments=False,
                ceilings=ceilings,
            ),
        ),
        (
            ValueError,
            "unique and canonical",
            lambda: PolicyPlanning(
                subjects=(request, request),
                environments=(),
                allow_any_subjects=False,
                allow_any_environments=False,
                ceilings=ceilings,
            ),
        ),
        (
            TypeError,
            "ceilings",
            lambda: PolicyPlanning(
                subjects=(),
                environments=(),
                allow_any_subjects=False,
                allow_any_environments=False,
                ceilings=cast("tuple[PlanningBudgetValue, ...]", [object()]),
            ),
        ),
        (
            TypeError,
            "wildcard flags",
            lambda: msgspec.structs.replace(
                trusted_local_policy().planning,
                allow_any_subjects=cast("bool", 1),
            ),
        ),
        (
            ValueError,
            "allowAnySubjects",
            lambda: PolicyPlanning(
                subjects=(request,),
                environments=(),
                allow_any_subjects=True,
                allow_any_environments=False,
                ceilings=ceilings,
            ),
        ),
        (
            ValueError,
            "allowAnyEnvironments",
            lambda: PolicyPlanning(
                subjects=(),
                environments=(environment,),
                allow_any_subjects=False,
                allow_any_environments=True,
                ceilings=ceilings,
            ),
        ),
    )
    for error, match, callback in cases:
        _assert_rejected(error, match, callback)


def test_http_policy_rejects_untyped_empty_or_noncanonical_allowlists() -> None:
    policy = trusted_local_policy().http
    cases: tuple[tuple[type[Exception], str, Callable[[], object]], ...] = (
        (TypeError, "HTTP schemes", lambda: msgspec.structs.replace(policy, allowed_schemes=[])),
        (ValueError, "HTTP schemes", lambda: msgspec.structs.replace(policy, allowed_schemes=())),
        (
            ValueError,
            "HTTP schemes",
            lambda: msgspec.structs.replace(
                policy, allowed_schemes=(HttpScheme.HTTPS, HttpScheme.HTTP)
            ),
        ),
        (TypeError, "HTTP methods", lambda: msgspec.structs.replace(policy, allowed_methods=[])),
        (ValueError, "HTTP methods", lambda: msgspec.structs.replace(policy, allowed_methods=())),
        (
            ValueError,
            "HTTP methods",
            lambda: msgspec.structs.replace(
                policy,
                allowed_methods=(HttpMethod(value="POST"), HttpMethod(value="GET")),
            ),
        ),
        (
            TypeError,
            "HTTP hosts",
            lambda: msgspec.structs.replace(policy, allowed_hosts=(object(),)),
        ),
        (
            ValueError,
            "HTTP hosts",
            lambda: msgspec.structs.replace(
                policy,
                allowed_hosts=(DnsHost(value="z.example"), DnsHost(value="a.example")),
            ),
        ),
        (TypeError, "HTTP ports", lambda: msgspec.structs.replace(policy, allowed_ports=[])),
        (
            ValueError,
            "requires allowedPorts",
            lambda: msgspec.structs.replace(policy, allowed_ports=(), allow_any_port=False),
        ),
        (
            ValueError,
            "HTTP ports",
            lambda: msgspec.structs.replace(
                policy,
                allowed_ports=(HttpPort(value=443), HttpPort(value=80)),
                allow_any_port=False,
            ),
        ),
        (
            TypeError,
            "address classes",
            lambda: msgspec.structs.replace(policy, allowed_address_classes=[]),
        ),
        (
            ValueError,
            "address classes",
            lambda: msgspec.structs.replace(policy, allowed_address_classes=()),
        ),
        (
            TypeError,
            "authority flags",
            lambda: msgspec.structs.replace(policy, allow_proxy_dns=cast("bool", 1)),
        ),
    )
    for error, match, callback in cases:
        _assert_rejected(error, match, callback)


def test_value_and_artifact_policies_reject_ambiguous_or_untyped_values() -> None:
    schema = NamedCapabilityTarget(
        kind=CapabilityTargetKind.SCHEMA_SOURCE,
        identifier="https://schemas.example.com/model.json",
    )
    cases: tuple[tuple[type[Exception], str, Callable[[], object]], ...] = (
        (
            ValueError,
            "portable variable",
            lambda: AmbientEnvironmentPolicy(
                names=cast("tuple[str, ...]", ["TOKEN"]),
                allow_any=False,
            ),
        ),
        (
            ValueError,
            "unique and canonical",
            lambda: AmbientEnvironmentPolicy(names=("TOKEN", "TOKEN"), allow_any=False),
        ),
        (
            TypeError,
            "allowAny",
            lambda: AmbientEnvironmentPolicy(names=(), allow_any=cast("bool", 1)),
        ),
        (
            TypeError,
            "schema targets",
            lambda: RemoteSchemaPolicy(
                sources=cast("tuple[NamedCapabilityTarget, ...]", [schema]),
                allow_any=False,
            ),
        ),
        (
            ValueError,
            "unique and canonical",
            lambda: RemoteSchemaPolicy(sources=(schema, schema), allow_any=False),
        ),
        (
            TypeError,
            "allowAny",
            lambda: RemoteSchemaPolicy(sources=(), allow_any=cast("bool", 1)),
        ),
        (
            ValueError,
            "redundant allowlist",
            lambda: RemoteSchemaPolicy(sources=(schema,), allow_any=True),
        ),
        (
            TypeError,
            "ambient environment",
            lambda: PolicyValues(
                ambient_environment=cast("AmbientEnvironmentPolicy", object()),
                allow_project_files=False,
                remote_schemas=RemoteSchemaPolicy(sources=(), allow_any=False),
            ),
        ),
        (
            TypeError,
            "project-file",
            lambda: PolicyValues(
                ambient_environment=AmbientEnvironmentPolicy(names=(), allow_any=False),
                allow_project_files=cast("bool", 1),
                remote_schemas=RemoteSchemaPolicy(sources=(), allow_any=False),
            ),
        ),
        (
            TypeError,
            "remote schema",
            lambda: PolicyValues(
                ambient_environment=AmbientEnvironmentPolicy(names=(), allow_any=False),
                allow_project_files=False,
                remote_schemas=cast("RemoteSchemaPolicy", object()),
            ),
        ),
        (
            TypeError,
            "permission",
            lambda: PolicyArtifacts(allow_sanitized_persistence=cast("bool", 1), roots=()),
        ),
        (
            ValueError,
            "bounded absolute paths",
            lambda: PolicyArtifacts(allow_sanitized_persistence=True, roots=("relative",)),
        ),
        (
            ValueError,
            "bounded absolute paths",
            lambda: PolicyArtifacts(
                allow_sanitized_persistence=True,
                roots=cast("tuple[str, ...]", (1,)),
            ),
        ),
        (
            ValueError,
            "bounded absolute paths",
            lambda: PolicyArtifacts(allow_sanitized_persistence=True, roots=("/bad\nroot",)),
        ),
        (
            ValueError,
            "unique and canonical",
            lambda: PolicyArtifacts(allow_sanitized_persistence=True, roots=("/z", "/a")),
        ),
    )
    for error, match, callback in cases:
        _assert_rejected(error, match, callback)
    assert PolicyArtifacts(
        allow_sanitized_persistence=True,
        roots=("C:/comparo",),
    ).roots == ("C:/comparo",)


def test_compilation_products_reject_incoherent_direct_construction() -> None:
    policy = trusted_local_policy()
    diagnostic = cast("ConfigurationDiagnostic", object())
    cases: tuple[tuple[type[Exception], str, Callable[[], object]], ...] = (
        (
            ValueError,
            "API version",
            lambda: msgspec.structs.replace(policy, api_version="comparo.policy/v9"),
        ),
        (
            TypeError,
            "DefinitionRef",
            lambda: msgspec.structs.replace(policy, definition=object()),
        ),
        (
            ValueError,
            "identify ExecutionPolicy",
            lambda: msgspec.structs.replace(
                policy,
                definition=msgspec.structs.replace(
                    policy.definition,
                    identity=ResourceIdentity(
                        namespace=policy.definition.identity.namespace,
                        kind=ResourceKind.PROJECT,
                        resource_id=policy.definition.identity.resource_id,
                    ),
                ),
            ),
        ),
        (
            TypeError,
            "metadata",
            lambda: msgspec.structs.replace(policy, metadata=cast("Metadata", object())),
        ),
        (
            TypeError,
            "location",
            lambda: msgspec.structs.replace(policy, location=cast("SourceLocation", object())),
        ),
        (
            TypeError,
            "planning section",
            lambda: msgspec.structs.replace(policy, planning=object()),
        ),
        (TypeError, "HTTP section", lambda: msgspec.structs.replace(policy, http=object())),
        (TypeError, "values section", lambda: msgspec.structs.replace(policy, values=object())),
        (
            TypeError,
            "artifact section",
            lambda: msgspec.structs.replace(policy, artifacts=object()),
        ),
        (
            TypeError,
            "CompiledExecutionPolicy",
            lambda: PolicyCompilationResult(policy=cast("CompiledExecutionPolicy", object())),
        ),
        (
            TypeError,
            "diagnostics",
            lambda: PolicyCompilationResult(
                diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", (diagnostic,))
            ),
        ),
    )
    for error, match, callback in cases:
        _assert_rejected(error, match, callback)


def test_unrestricted_planning_authority_retains_none_allowlists() -> None:
    policy = trusted_local_policy()
    from comparo.core.policy import planning_authority

    authority = planning_authority(policy, project_namespace=NamespaceId(value="local"))
    assert authority.allowed_subjects is None
    assert authority.allowed_environments is None
