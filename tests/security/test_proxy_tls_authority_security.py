"""Security regressions for independent proxy and target TLS authority."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion as PolicyTlsVersion
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsVersion as ObservedTlsVersion
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.identity import ExchangeId
from tests.core.domain.http.factories import policies
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import proxy_request_route_endpoint
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.observation_factories import tls


def _tls_policy(identity: str, *, minimum: PolicyTlsVersion) -> TlsPolicy:
    return msgspec.structs.replace(
        policies().tls,
        minimum_version=minimum,
        client_identity=ClientIdentityRef(value=identity),
    )


def test_target_mtls_identity_cannot_be_reported_as_proxy_tls_authority() -> None:
    target_policy = _tls_policy("origin-client", minimum=PolicyTlsVersion.TLS_1_2)
    proxy_policy = _tls_policy("proxy-client", minimum=PolicyTlsVersion.TLS_1_2)
    effective = request()
    effective = msgspec.structs.replace(
        effective,
        policies=msgspec.structs.replace(effective.policies, tls=target_policy),
    )
    proxied, route, proxy_endpoint = proxy_request_route_endpoint(
        effective,
        proxy_scheme=HttpScheme.HTTPS,
    )
    explicit_proxy = proxied.policies.proxy
    assert type(explicit_proxy) is ExplicitProxy
    explicit_proxy = msgspec.structs.replace(
        explicit_proxy,
        tls=ProxyTlsPolicy(policy=proxy_policy),
    )
    assert type(explicit_proxy.tls) is ProxyTlsPolicy
    proxied = msgspec.structs.replace(
        proxied,
        policies=msgspec.structs.replace(proxied.policies, proxy=explicit_proxy),
    )
    valid_response = msgspec.structs.replace(
        response(),
        endpoint=proxy_endpoint,
        tls=TlsLayers(
            connect=tls(applied_policy=explicit_proxy.tls),
            origin=tls(applied_policy=target_policy),
        ),
    )
    authorized = authorized_request(
        proxied,
        route,
        request_occurrence=occurrence(exchange=920),
    )
    valid = HttpExchange(
        exchange_id=ExchangeId(value=UUID(int=920)),
        request=authorized,
        response=valid_response,
        failure=None,
        redirect=None,
        timings=exchange_timings(proxy=True),
    )
    assert valid.response is valid_response

    leaked_to_proxy = msgspec.structs.replace(
        valid_response,
        tls=TlsLayers(
            connect=tls(applied_policy=target_policy),
            origin=tls(applied_policy=target_policy),
        ),
    )
    with pytest.raises(ValueError, match="proxy TLS policy evidence"):
        msgspec.structs.replace(valid, response=leaked_to_proxy)

    proxy_spoofed_as_origin = msgspec.structs.replace(
        valid_response,
        tls=TlsLayers(
            connect=tls(applied_policy=explicit_proxy.tls),
            origin=tls(applied_policy=explicit_proxy.tls),
        ),
    )
    with pytest.raises(ValueError, match="target TLS policy evidence"):
        msgspec.structs.replace(valid, response=proxy_spoofed_as_origin)


def test_proxy_tls_minimum_controls_clear_target_over_https_proxy() -> None:
    effective = request(scheme=HttpScheme.HTTP)
    proxied, route, proxy_endpoint = proxy_request_route_endpoint(
        effective,
        proxy_scheme=HttpScheme.HTTPS,
    )
    explicit_proxy = proxied.policies.proxy
    assert type(explicit_proxy) is ExplicitProxy
    strict_proxy = ProxyTlsPolicy(
        policy=_tls_policy("strict-proxy", minimum=PolicyTlsVersion.TLS_1_3)
    )
    explicit_proxy = msgspec.structs.replace(explicit_proxy, tls=strict_proxy)
    assert type(explicit_proxy.tls) is ProxyTlsPolicy
    proxied = msgspec.structs.replace(
        proxied,
        policies=msgspec.structs.replace(proxied.policies, proxy=explicit_proxy),
    )
    observed = msgspec.structs.replace(
        response(scheme=HttpScheme.HTTP),
        endpoint=proxy_endpoint,
        tls=TlsLayers(
            connect=msgspec.structs.replace(
                tls(HttpProtocol.HTTP_1_1, applied_policy=strict_proxy),
                version=ObservedTlsVersion.TLS_1_2,
            )
        ),
    )
    with pytest.raises(ValueError, match="below the authorized minimum"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=921)),
            request=authorized_request(
                proxied,
                route,
                request_occurrence=occurrence(exchange=921),
            ),
            response=observed,
            failure=None,
            redirect=None,
            timings=exchange_timings(proxy=True),
        )
