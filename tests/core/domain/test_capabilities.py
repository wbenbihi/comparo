"""Tests for typed capability requests and authority decisions."""

import pytest

from comparo.core.domain.capabilities import CapabilityCategory
from comparo.core.domain.capabilities import CapabilityDenialReason
from comparo.core.domain.capabilities import CapabilityDenied
from comparo.core.domain.capabilities import CapabilityGranted
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import CapabilityRequest
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind


def _capability_id() -> CapabilityId:
    return CapabilityId(namespace="comparo.http", name="request", version="1")


def _operation() -> CapabilityOperation:
    return CapabilityOperation(value="send.request")


def _authority() -> tuple[ProvenanceEntry, ...]:
    return (ProvenanceEntry(kind=ProvenanceKind.EXECUTION_POLICY),)


def _request() -> CapabilityRequest:
    return CapabilityRequest(
        category=CapabilityCategory.HTTP,
        capability_id=_capability_id(),
        operation=_operation(),
        target=NamedCapabilityTarget(
            kind=CapabilityTargetKind.DESTINATION,
            identifier="api.example.test:443",
        ),
    )


@pytest.mark.parametrize(
    ("namespace", "name", "version"),
    [
        ("Bad", "request", "1"),
        ("comparo", "bad_name", "1"),
        ("comparo", "request", "v1"),
        ("comparo", "request", chr(0x661)),
        ("comparo", "request", 1),
    ],
)
def test_capability_identity_rejects_ambiguous_values(
    namespace: object,
    name: object,
    version: object,
) -> None:
    with pytest.raises(ValueError, match="capability"):
        CapabilityId(namespace=namespace, name=name, version=version)  # type: ignore[arg-type]


def test_capability_operation_requires_a_machine_token() -> None:
    assert _operation().value == "send.request"
    with pytest.raises(ValueError, match="lowercase dotted token"):
        CapabilityOperation(value="send request")


def test_named_target_is_a_non_payload_identifier() -> None:
    target = NamedCapabilityTarget(
        kind=CapabilityTargetKind.FILE_ROOT,
        identifier="project-root",
    )
    assert target.identifier == "project-root"
    with pytest.raises(TypeError, match="CapabilityTargetKind"):
        NamedCapabilityTarget(kind="file-root", identifier="project-root")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-blank"):
        NamedCapabilityTarget(kind=CapabilityTargetKind.FILE_ROOT, identifier=" ")


def test_capability_request_accepts_resource_and_named_targets() -> None:
    resource = ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.SCHEMA,
        resource_id=ResourceId(value="public-api"),
    )
    request = CapabilityRequest(
        category=CapabilityCategory.REMOTE_SCHEMA,
        capability_id=_capability_id(),
        operation=_operation(),
        target=resource,
    )
    assert request.target == resource
    assert _request().category is CapabilityCategory.HTTP


def test_capability_request_rejects_untyped_components() -> None:
    capability_id = _capability_id()
    operation = _operation()
    with pytest.raises(TypeError, match="category"):
        CapabilityRequest(
            category="http",  # type: ignore[arg-type]
            capability_id=capability_id,
            operation=operation,
        )
    with pytest.raises(TypeError, match="capability ID"):
        CapabilityRequest(
            category=CapabilityCategory.HTTP,
            capability_id="comparo.http/request/1",  # type: ignore[arg-type]
            operation=operation,
        )
    with pytest.raises(TypeError, match="operation"):
        CapabilityRequest(
            category=CapabilityCategory.HTTP,
            capability_id=capability_id,
            operation="send",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="target"):
        CapabilityRequest(
            category=CapabilityCategory.HTTP,
            capability_id=capability_id,
            operation=operation,
            target="api.example.test",  # type: ignore[arg-type]
        )


def test_grant_and_denial_are_distinct_typed_decisions() -> None:
    request = _request()
    granted = CapabilityGranted(request=request, authority=_authority())
    denied = CapabilityDenied(
        request=request,
        reason=CapabilityDenialReason.OUTSIDE_ALLOWED_TARGETS,
        authority=_authority(),
    )
    assert granted.request == denied.request
    assert denied.reason is CapabilityDenialReason.OUTSIDE_ALLOWED_TARGETS
    assert isinstance(granted, CapabilityGranted)
    assert isinstance(denied, CapabilityDenied)


def test_capability_decisions_validate_request_reason_and_authority() -> None:
    request = _request()
    with pytest.raises(TypeError, match="CapabilityRequest"):
        CapabilityGranted(request="request", authority=_authority())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not be empty"):
        CapabilityGranted(request=request, authority=())
    with pytest.raises(TypeError, match="immutable tuple"):
        CapabilityGranted(request=request, authority=list(_authority()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ProvenanceEntry"):
        CapabilityGranted(request=request, authority=("policy",))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="CapabilityRequest"):
        CapabilityDenied(
            request="request",  # type: ignore[arg-type]
            reason=CapabilityDenialReason.NOT_ALLOWED,
            authority=_authority(),
        )
    with pytest.raises(TypeError, match="denial reason"):
        CapabilityDenied(
            request=request,
            reason="not-allowed",  # type: ignore[arg-type]
            authority=_authority(),
        )


def test_capability_decisions_are_immutable() -> None:
    decision = CapabilityGranted(request=_request(), authority=_authority())
    with pytest.raises(AttributeError):
        decision.authority = ()  # type: ignore[misc]
