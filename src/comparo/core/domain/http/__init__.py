"""Stable aggregate-level exports for Comparo's protocol-neutral HTTP domain.

Leaf value objects remain available from their focused modules. Keeping this facade small avoids
turning every internal representation into a compatibility promise.
"""

from __future__ import annotations

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import Authentication
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.authorization import SecretEgressApproval
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.pre_send import AuthorizedResolutionRequest
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpProxyDnsApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.pre_send import HttpResolutionGrant
from comparo.core.domain.http.pre_send import HttpResolutionIntent
from comparo.core.domain.http.request_materialization import RequestMaterializationProof
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import LogicalRequest
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import RequestTarget
from comparo.core.domain.http.url import ResolvedRoute

__all__ = (
    "AbsoluteHttpUrl",
    "ApiKeyAuthentication",
    "Authentication",
    "AuthorizedExchangeRequest",
    "AuthorizedResolutionRequest",
    "BasicAuthentication",
    "BearerAuthentication",
    "DirectResolvedRoute",
    "EffectiveRequest",
    "FinalizedWireRequest",
    "HttpAuthorizationDenial",
    "HttpAuthorizationGrant",
    "HttpAuthorizationIntent",
    "HttpMutationApprovalDenial",
    "HttpProxyDnsApprovalDenial",
    "HttpRequestOccurrence",
    "HttpResolutionDenial",
    "HttpResolutionFailure",
    "HttpResolutionGrant",
    "HttpResolutionIntent",
    "LogicalRequest",
    "NoAuthentication",
    "ProxyResolvedRoute",
    "RequestBody",
    "RequestMaterializationProof",
    "RequestPolicies",
    "RequestTarget",
    "ResolvedRoute",
    "SecretEgressApproval",
)
