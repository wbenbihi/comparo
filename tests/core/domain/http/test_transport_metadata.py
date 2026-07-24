"""Tests for HTTP endpoint, TLS, and transport-failure metadata."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.transport import EndpointMetadata
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsMetadata
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.transport import TlsUnavailable
from comparo.core.domain.http.transport import TlsUnavailableReason
from comparo.core.domain.http.transport import TlsVersion
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.primitives import Sha256Digest
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import policies
from tests.core.domain.http.observation_factories import endpoint
from tests.core.domain.http.observation_factories import response


def test_transport_components_reject_all_untyped_or_impossible_facts() -> None:
    secure_endpoint = endpoint()
    secure_tls = response().tls.connect
    assert type(secure_tls) is TlsMetadata
    with pytest.raises(TypeError, match="TLS version"):
        msgspec.structs.replace(secure_tls, version=cast("TlsVersion", "tls1.3"))
    with pytest.raises(TypeError, match="TLS ALPN"):
        msgspec.structs.replace(secure_tls, alpn=cast("HttpProtocol", "h2"))
    with pytest.raises(TypeError, match="certificate digest"):
        msgspec.structs.replace(
            secure_tls,
            peer_certificate_sha256=cast("Sha256Digest", "digest"),
        )
    with pytest.raises(TypeError, match="session-resumed"):
        msgspec.structs.replace(secure_tls, session_resumed=cast("bool", 1))
    with pytest.raises(TypeError, match="TLS applied policy"):
        msgspec.structs.replace(secure_tls, applied_policy=cast("TlsPolicy", "policy"))
    with pytest.raises(TypeError, match="connect TLS layer"):
        TlsLayers(connect=cast("TlsMetadata", "tls"))
    with pytest.raises(TypeError, match="origin TLS layer"):
        TlsLayers(connect=secure_tls, origin=cast("TlsMetadata", TlsNotPerformed()))

    with pytest.raises(TypeError, match="endpoint destination"):
        msgspec.structs.replace(
            secure_endpoint,
            destination=cast("NetworkDestination", "destination"),
        )
    with pytest.raises(TypeError, match="remote endpoint address"):
        msgspec.structs.replace(
            secure_endpoint,
            remote_address=cast("Ipv4Host", DnsHost(value="api.example.test")),
        )
    with pytest.raises(ValueError, match="remote endpoint port"):
        msgspec.structs.replace(secure_endpoint, remote_port=HttpPort(value=8443))
    with pytest.raises(TypeError, match="local endpoint address"):
        msgspec.structs.replace(
            secure_endpoint,
            local_address=cast("Ipv4Host", DnsHost(value="local.example.test")),
            local_port=HttpPort(value=1234),
        )
    with pytest.raises(TypeError, match="local endpoint port"):
        msgspec.structs.replace(
            secure_endpoint,
            local_address=Ipv4Host(value="192.0.2.20"),
            local_port=cast("HttpPort", 1234),
        )
    with pytest.raises(TypeError, match="connection-reused"):
        msgspec.structs.replace(secure_endpoint, connection_reused=cast("bool", 1))

    clear = response(scheme=HttpScheme.HTTP)
    with pytest.raises(ValueError, match="clear-text connect destination"):
        msgspec.structs.replace(clear, tls=TlsLayers(connect=secure_tls))


def test_endpoint_and_tls_metadata_reject_inconsistent_transport_facts() -> None:
    with pytest.raises(ValueError, match="authorized resolved"):
        EndpointMetadata(
            destination=endpoint().destination,
            remote_address=Ipv4Host(value="192.0.2.99"),
            remote_port=HttpPort(value=443),
            local_address=None,
            local_port=None,
            connection_id="connection-2",
            connection_reused=False,
        )
    second_address = Ipv4Host(value="192.0.2.11")
    selected_destination = NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="api.example.test"),
        port=HttpPort(value=443),
        resolved_addresses=(Ipv4Host(value="192.0.2.10"), second_address),
        selected_address=second_address,
    )
    with pytest.raises(ValueError, match="pinned selected"):
        EndpointMetadata(
            destination=selected_destination,
            remote_address=Ipv4Host(value="192.0.2.10"),
            remote_port=HttpPort(value=443),
            local_address=None,
            local_port=None,
            connection_id="connection-3",
            connection_reused=False,
        )
    with pytest.raises(ValueError, match="HTTPS"):
        ResponseObservation(
            protocol=HttpProtocol.HTTP_2,
            status=HttpStatus(value=200),
            reason=None,
            informational=(),
            headers=ResponseFields(),
            trailers=ResponseFields(),
            media=MissingMediaType(),
            content_encodings=(),
            body=ResponseEntityBody(
                capture=PolicyOmittedCapture(),
                handle=None,
                encoded_byte_count=None,
            ),
            endpoint=endpoint(),
            tls=TlsLayers(connect=TlsNotPerformed()),
        )


def test_transport_metadata_and_failures_reject_untyped_components() -> None:
    valid_endpoint = endpoint()
    with pytest.raises(ValueError, match="cipher suite"):
        TlsMetadata(
            version=TlsVersion.TLS_1_3,
            cipher_suite="not valid",
            alpn=HttpProtocol.HTTP_2,
            peer_certificate_sha256=digest("a"),
            session_resumed=False,
            applied_policy=policies().tls,
        )
    with pytest.raises(TypeError, match="unavailable TLS"):
        TlsUnavailable(
            reason=cast("TlsUnavailableReason", "missing"),
            applied_policy=policies().tls,
        )
    with pytest.raises(TypeError, match="unavailable TLS applied policy"):
        TlsUnavailable(
            reason=TlsUnavailableReason.INSTRUMENTATION_DISABLED,
            applied_policy=cast("TlsPolicy", "policy"),
        )
    proxy_tls = ProxyTlsPolicy(policy=policies().tls)
    assert (
        TlsUnavailable(
            reason=TlsUnavailableReason.INSTRUMENTATION_DISABLED,
            applied_policy=proxy_tls,
        ).applied_policy
        is proxy_tls
    )
    with pytest.raises(ValueError, match="local endpoint address and port"):
        EndpointMetadata(
            destination=valid_endpoint.destination,
            remote_address=valid_endpoint.remote_address,
            remote_port=valid_endpoint.remote_port,
            local_address=Ipv4Host(value="192.0.2.20"),
            local_port=None,
            connection_id="connection-2",
            connection_reused=False,
        )
    with pytest.raises(ValueError, match="connection ID"):
        EndpointMetadata(
            destination=valid_endpoint.destination,
            remote_address=valid_endpoint.remote_address,
            remote_port=valid_endpoint.remote_port,
            local_address=None,
            local_port=None,
            connection_id="contains spaces",
            connection_reused=False,
        )
    with pytest.raises(TypeError, match="failure phase"):
        TransportFailure(
            phase=cast("FailurePhase", "dns"),
            code=DiagnosticCode(value="http.transport-failed"),
            retryable=True,
        )
    with pytest.raises(TypeError, match="failure code"):
        TransportFailure(
            phase=FailurePhase.DNS,
            code=cast("DiagnosticCode", "failed"),
            retryable=True,
        )
    with pytest.raises(TypeError, match="retryable"):
        TransportFailure(
            phase=FailurePhase.DNS,
            code=DiagnosticCode(value="http.transport-failed"),
            retryable=cast("bool", 1),
        )
