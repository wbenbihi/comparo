"""Branch-complete direct-construction tests for HTTP request policies."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import HeaderApiKeyPlacement
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import IdempotencyKeyRetry
from comparo.core.domain.http.policies import MutationApprovalRequirement
from comparo.core.domain.http.policies import MutationRetry
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import ProxyAuthentication
from comparo.core.domain.http.policies import ProxyPolicy
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.policies import RedirectPolicy
from comparo.core.domain.http.policies import RetryBackoff
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.policies import TimeoutLimit
from comparo.core.domain.http.policies import TimeoutPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.policies import TrustStoreRef
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from tests.core.domain.http.factories import api_key_secret
from tests.core.domain.http.factories import policies


def enabled_retry() -> RetryPolicy:
    """Build one valid enabled retry policy."""
    return RetryPolicy(
        max_attempts=PositiveCount(value=2),
        conditions=(RetryCondition.CONNECT_FAILURE,),
        backoff=NoBackoff(),
        jitter=Ratio(numerator=0, denominator=PositiveCount(value=1)),
    )


def test_timeout_and_retry_strategy_components_reject_wrong_types() -> None:
    """Validate every timeout, idempotency, and backoff component boundary."""
    defaults = policies()
    with pytest.raises(TypeError, match="timeout policy fields"):
        msgspec.structs.replace(
            defaults.timeouts,
            connect=cast("TimeoutLimit", object()),
        )
    with pytest.raises(TypeError, match="identify a HeaderName"):
        IdempotencyKeyRetry(header_name=cast("HeaderName", "idempotency-key"))
    with pytest.raises(ValueError, match="reserved request header"):
        IdempotencyKeyRetry(header_name=HeaderName(value="authorization"))
    with pytest.raises(TypeError, match="fixed backoff delay"):
        FixedBackoff(delay=cast("Duration", 1))
    with pytest.raises(TypeError, match="backoff bounds"):
        ExponentialBackoff(
            initial=cast("Duration", 1),
            multiplier=Ratio(numerator=1, denominator=PositiveCount(value=1)),
            maximum=Duration(nanoseconds=2),
        )
    with pytest.raises(TypeError, match="backoff multiplier"):
        ExponentialBackoff(
            initial=Duration(nanoseconds=1),
            multiplier=cast("Ratio", 1),
            maximum=Duration(nanoseconds=2),
        )


def test_retry_policy_rejects_every_untyped_component() -> None:
    """Keep retry count, conditions, schedule, jitter, and mutation strategy closed."""
    valid = enabled_retry()
    with pytest.raises(TypeError, match="maximum attempts"):
        msgspec.structs.replace(valid, max_attempts=cast("PositiveCount", 2))
    with pytest.raises(TypeError, match="immutable tuple"):
        msgspec.structs.replace(
            valid,
            conditions=cast("tuple[RetryCondition, ...]", [RetryCondition.CONNECT_FAILURE]),
        )
    with pytest.raises(TypeError, match="supported RetryBackoff"):
        msgspec.structs.replace(valid, backoff=cast("RetryBackoff", object()))
    with pytest.raises(TypeError, match="retry jitter"):
        msgspec.structs.replace(valid, jitter=cast("Ratio", object()))
    with pytest.raises(TypeError, match="supported tagged strategy"):
        msgspec.structs.replace(valid, mutation_retry=cast("MutationRetry", object()))


def test_tls_policy_rejects_untyped_identity_and_verification_components() -> None:
    """Exercise trust references, verification flags, protocol, trust source, and mTLS identity."""
    for constructor in (TrustStoreRef, ClientIdentityRef):
        with pytest.raises(ValueError, match="non-blank"):
            constructor(value=cast("str", 1))
    valid = policies().tls
    with pytest.raises(TypeError, match="verification flags"):
        msgspec.structs.replace(valid, verify_certificates=cast("bool", 1))
    with pytest.raises(TypeError, match="minimum version"):
        msgspec.structs.replace(valid, minimum_version=cast("TlsVersion", "tls-1.2"))
    with pytest.raises(TypeError, match="trust source"):
        msgspec.structs.replace(valid, trust_source=cast("TrustSource", "operating-system"))
    with pytest.raises(TypeError, match="client identity"):
        msgspec.structs.replace(valid, client_identity=cast("ClientIdentityRef", object()))


def test_proxy_redirect_and_policy_bundle_reject_wrong_types() -> None:
    """Close proxy, redirect, and aggregate policy variants under direct construction."""
    authority = HttpAuthority(host=DnsHost(value="proxy.example.test"))
    with pytest.raises(TypeError, match="proxy scheme"):
        ExplicitProxy(
            scheme=cast("HttpScheme", "https"),
            authority=authority,
            authentication=NoAuthentication(),
        )
    with pytest.raises(TypeError, match="proxy authority"):
        ExplicitProxy(
            scheme=HttpScheme.HTTPS,
            authority=cast("HttpAuthority", object()),
            authentication=NoAuthentication(),
        )
    with pytest.raises(TypeError, match="proxy authentication"):
        ExplicitProxy(
            scheme=HttpScheme.HTTPS,
            authority=authority,
            authentication=cast(
                "ProxyAuthentication",
                ApiKeyAuthentication(
                    credential=api_key_secret(),
                    placement=HeaderApiKeyPlacement(name=HeaderName(value="x-api-key")),
                ),
            ),
        )

    redirect = RedirectPolicy(max_hops=Count(value=1))
    with pytest.raises(TypeError, match="maximum hops"):
        msgspec.structs.replace(redirect, max_hops=cast("Count", 1))
    with pytest.raises(TypeError, match="method mode"):
        msgspec.structs.replace(
            redirect,
            method_mode=cast("RedirectMethodMode", "strict-preserve"),
        )
    for field in ("allow_cross_origin", "strip_sensitive_credentials", "block_https_downgrade"):
        with pytest.raises(TypeError, match="safeguard flags"):
            msgspec.structs.replace(redirect, **{field: 1})

    defaults = policies()
    invalid_components = (
        {"timeouts": cast("TimeoutPolicy", object())},
        {"retries": cast("RetryPolicy", object())},
        {"tls": cast("TlsPolicy", object())},
        {"proxy": cast("ProxyPolicy", object())},
        {"redirects": cast("RedirectPolicy", object())},
        {"mutation_approval": cast("MutationApprovalRequirement", object())},
    )
    for replacement in invalid_components:
        with pytest.raises(TypeError):
            msgspec.structs.replace(defaults, **replacement)
