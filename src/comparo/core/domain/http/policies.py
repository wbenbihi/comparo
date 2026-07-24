"""Explicit request timeout, retry, TLS, proxy, and redirect policies."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio


@final
class UnlimitedTimeout(msgspec.Struct, frozen=True, kw_only=True):
    """An explicit request for no deadline, subject to effective external authority."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


type TimeoutLimit = Duration | UnlimitedTimeout


def _is_timeout_limit(value: object) -> TypeGuard[TimeoutLimit]:
    return type(value) in {Duration, UnlimitedTimeout}


@final
class TimeoutPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Independent phase deadlines plus attempt and execution envelopes."""

    connect: TimeoutLimit
    tls_handshake: TimeoutLimit
    response_headers: TimeoutLimit
    idle: TimeoutLimit
    attempt_total: TimeoutLimit
    execution_total: TimeoutLimit

    def __post_init__(self) -> None:
        """Require explicit typed limits and coherent total envelopes."""
        limits = (
            self.connect,
            self.tls_handshake,
            self.response_headers,
            self.idle,
            self.attempt_total,
            self.execution_total,
        )
        if any(not _is_timeout_limit(limit) for limit in limits):
            raise TypeError("timeout policy fields must be Duration or UnlimitedTimeout")
        if (
            type(self.attempt_total) is Duration
            and type(self.execution_total) is Duration
            and self.execution_total.nanoseconds < self.attempt_total.nanoseconds
        ):
            raise ValueError("execution total timeout cannot be shorter than attempt total")


class RetryCondition(StrEnum):
    """Observable conditions on which a new attempt may be scheduled."""

    DNS_FAILURE = "dns-failure"
    CONNECT_FAILURE = "connect-failure"
    TLS_FAILURE = "tls-failure"
    RESPONSE_HEADERS_TIMEOUT = "response-headers-timeout"
    IDLE_TIMEOUT = "idle-timeout"
    STATUS_429 = "status-429"
    STATUS_5XX = "status-5xx"


@final
class NoMutationRetry(msgspec.Struct, frozen=True, kw_only=True):
    """Do not retry a request method that may mutate server state."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


@final
class IdempotencyKeyRetry(msgspec.Struct, frozen=True, kw_only=True):
    """Retry mutations only with one exact sensitivity-contained idempotency header."""

    header_name: HeaderName

    def __post_init__(self) -> None:
        """Require a non-reserved typed header identity."""
        if type(self.header_name) is not HeaderName:
            raise TypeError("idempotency key retry must identify a HeaderName")
        if self.header_name.value in {
            "authorization",
            "content-length",
            "content-type",
            "cookie",
            "host",
            "transfer-encoding",
        }:
            raise ValueError("idempotency key cannot use a reserved request header")


@final
class DeclaredIdempotentRetry(msgspec.Struct, frozen=True, kw_only=True):
    """Record an explicit contract that the mutation itself is idempotent."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


type MutationRetry = NoMutationRetry | IdempotencyKeyRetry | DeclaredIdempotentRetry


@final
class NoBackoff(msgspec.Struct, frozen=True, kw_only=True):
    """Schedule an allowed retry without a delay."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


@final
class FixedBackoff(msgspec.Struct, frozen=True, kw_only=True):
    """Use one exact delay before each retry."""

    delay: Duration

    def __post_init__(self) -> None:
        """Require an exact non-negative delay."""
        if type(self.delay) is not Duration:
            raise TypeError("fixed backoff delay must be Duration")


@final
class ExponentialBackoff(msgspec.Struct, frozen=True, kw_only=True):
    """Use capped deterministic exponential backoff before jitter."""

    initial: Duration
    multiplier: Ratio
    maximum: Duration

    def __post_init__(self) -> None:
        """Require exact coherent bounds and a non-decreasing multiplier."""
        if type(self.initial) is not Duration or type(self.maximum) is not Duration:
            raise TypeError("exponential backoff bounds must be Duration")
        if type(self.multiplier) is not Ratio:
            raise TypeError("exponential backoff multiplier must be Ratio")
        if self.multiplier.numerator < self.multiplier.denominator.value:
            raise ValueError("exponential backoff multiplier must be at least one")
        if self.maximum.nanoseconds < self.initial.nanoseconds:
            raise ValueError("exponential backoff maximum cannot be shorter than its initial delay")


type RetryBackoff = NoBackoff | FixedBackoff | ExponentialBackoff


@final
class RetryPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """An explicit observable retry schedule; one attempt means retries are disabled."""

    max_attempts: PositiveCount
    conditions: tuple[RetryCondition, ...]
    backoff: RetryBackoff
    jitter: Ratio
    mutation_retry: MutationRetry = NoMutationRetry()

    def __post_init__(self) -> None:
        """Require a coherent explicit retry schedule."""
        if type(self.max_attempts) is not PositiveCount:
            raise TypeError("retry maximum attempts must be PositiveCount")
        if type(self.conditions) is not tuple or any(
            type(condition) is not RetryCondition for condition in self.conditions
        ):
            raise TypeError("retry conditions must be an immutable tuple of RetryCondition values")
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("retry conditions must be unique")
        if self.max_attempts.value == 1 and self.conditions:
            raise ValueError("disabled retries cannot declare retry conditions")
        if self.max_attempts.value > 1 and not self.conditions:
            raise ValueError("enabled retries require at least one retry condition")
        if type(self.backoff) not in {NoBackoff, FixedBackoff, ExponentialBackoff}:
            raise TypeError("retry backoff must be a supported RetryBackoff")
        if type(self.jitter) is not Ratio:
            raise TypeError("retry jitter must be Ratio")
        if self.jitter.numerator > self.jitter.denominator.value:
            raise ValueError("retry jitter must be between zero and one")
        if type(self.mutation_retry) not in {
            NoMutationRetry,
            IdempotencyKeyRetry,
            DeclaredIdempotentRetry,
        }:
            raise TypeError("mutation retry must be a supported tagged strategy")


class TlsVersion(StrEnum):
    """Supported minimum TLS protocol versions."""

    TLS_1_2 = "tls-1.2"
    TLS_1_3 = "tls-1.3"


class TrustSource(StrEnum):
    """The explicit source of server-certificate trust anchors."""

    OPERATING_SYSTEM = "operating-system"
    CUSTOM = "custom"


@final
class TrustStoreRef(msgspec.Struct, frozen=True, kw_only=True):
    """A logical reference resolved by an authorized trust-store adapter."""

    value: str

    def __post_init__(self) -> None:
        """Require a non-blank logical trust-store identity."""
        if type(self.value) is not str or not self.value.strip():
            raise ValueError("trust-store reference must be non-blank text")


@final
class ClientIdentityRef(msgspec.Struct, frozen=True, kw_only=True):
    """A logical client-certificate identity without local path material."""

    value: str

    def __post_init__(self) -> None:
        """Require a non-blank logical client identity."""
        if type(self.value) is not str or not self.value.strip():
            raise ValueError("client-identity reference must be non-blank text")


@final
class TlsPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Explicit TLS verification, trust, and optional client identity behavior."""

    verify_certificates: bool
    verify_hostname: bool
    minimum_version: TlsVersion
    trust_source: TrustSource
    custom_trust_store: TrustStoreRef | None = None
    client_identity: ClientIdentityRef | None = None

    def __post_init__(self) -> None:
        """Require coherent verification and explicit trust configuration."""
        if type(self.verify_certificates) is not bool or type(self.verify_hostname) is not bool:
            raise TypeError("TLS verification flags must be bool")
        if self.verify_hostname and not self.verify_certificates:
            raise ValueError("hostname verification requires certificate verification")
        if type(self.minimum_version) is not TlsVersion:
            raise TypeError("TLS minimum version must be TlsVersion")
        if type(self.trust_source) is not TrustSource:
            raise TypeError("TLS trust source must be TrustSource")
        if self.trust_source is TrustSource.CUSTOM:
            if type(self.custom_trust_store) is not TrustStoreRef:
                raise ValueError("custom TLS trust requires a TrustStoreRef")
        elif self.custom_trust_store is not None:
            raise ValueError("operating-system trust cannot carry a custom trust-store reference")
        if self.client_identity is not None and type(self.client_identity) is not ClientIdentityRef:
            raise TypeError("TLS client identity must be ClientIdentityRef")


@final
class NoProxy(msgspec.Struct, frozen=True, kw_only=True):
    """Disable proxying and all ambient proxy discovery."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


type ProxyAuthentication = NoAuthentication | BasicAuthentication | BearerAuthentication


@final
class ExplicitProxy(msgspec.Struct, frozen=True, kw_only=True):
    """One typed proxy endpoint with no ambient configuration fallback."""

    scheme: HttpScheme
    authority: HttpAuthority
    authentication: ProxyAuthentication

    def __post_init__(self) -> None:
        """Require an explicit endpoint and closed proxy-authentication variant."""
        if type(self.scheme) is not HttpScheme:
            raise TypeError("proxy scheme must be HttpScheme")
        if type(self.authority) is not HttpAuthority:
            raise TypeError("proxy authority must be HttpAuthority")
        if type(self.authentication) not in {
            NoAuthentication,
            BasicAuthentication,
            BearerAuthentication,
        }:
            raise TypeError("proxy authentication must be none, Basic, or Bearer")


type ProxyPolicy = NoProxy | ExplicitProxy


class RedirectMethodMode(StrEnum):
    """Method handling for historically ambiguous 301 and 302 responses."""

    STRICT_PRESERVE = "strict-preserve"
    BROWSER_COMPATIBLE = "browser-compatible"


@final
class RedirectPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Explicit hop limits and cross-origin credential safeguards."""

    max_hops: Count
    method_mode: RedirectMethodMode = RedirectMethodMode.STRICT_PRESERVE
    allow_cross_origin: bool = False
    strip_sensitive_credentials: bool = True
    block_https_downgrade: bool = True

    def __post_init__(self) -> None:
        """Require typed hop limits, method behavior, and safeguard flags."""
        if type(self.max_hops) is not Count:
            raise TypeError("redirect maximum hops must be Count")
        if type(self.method_mode) is not RedirectMethodMode:
            raise TypeError("redirect method mode must be RedirectMethodMode")
        for value in (
            self.allow_cross_origin,
            self.strip_sensitive_credentials,
            self.block_https_downgrade,
        ):
            if type(value) is not bool:
                raise TypeError("redirect safeguard flags must be bool")


@final
class NoMutationApprovalRequired(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy does not require a separate approval for unsafe HTTP methods."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


@final
class MutationApprovalRequired(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy requires an explicit authority decision for unsafe HTTP methods."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


type MutationApprovalRequirement = NoMutationApprovalRequired | MutationApprovalRequired


@final
class RequestPolicies(msgspec.Struct, frozen=True, kw_only=True):
    """The complete explicit policy bundle needed for request orchestration."""

    timeouts: TimeoutPolicy
    retries: RetryPolicy
    tls: TlsPolicy
    proxy: ProxyPolicy
    redirects: RedirectPolicy
    mutation_approval: MutationApprovalRequirement = NoMutationApprovalRequired()

    def __post_init__(self) -> None:
        """Require all five explicit policy components."""
        if type(self.timeouts) is not TimeoutPolicy:
            raise TypeError("request timeout policy must be TimeoutPolicy")
        if type(self.retries) is not RetryPolicy:
            raise TypeError("request retry policy must be RetryPolicy")
        if type(self.tls) is not TlsPolicy:
            raise TypeError("request TLS policy must be TlsPolicy")
        if type(self.proxy) not in {NoProxy, ExplicitProxy}:
            raise TypeError("request proxy policy must be ProxyPolicy")
        if type(self.redirects) is not RedirectPolicy:
            raise TypeError("request redirect policy must be RedirectPolicy")
        if type(self.mutation_approval) not in {
            NoMutationApprovalRequired,
            MutationApprovalRequired,
        }:
            raise TypeError("request mutation approval must be an explicit requirement")
