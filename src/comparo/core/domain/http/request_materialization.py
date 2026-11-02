"""Value-free request-body materialization identity and proof records."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.bodies import BodyDigest
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import JsonSerialization
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.body_lineage import request_body_taint_origins
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value

type EffectiveRequestFingerprint = Fingerprint | SensitiveValue[Fingerprint]


class RequestBodyKind(DomainStrEnum):
    """Closed request-body shapes relevant to deterministic wire materialization."""

    NONE = "none"
    JSON = "json"
    TEXT = "text"
    BYTES = "bytes"
    FORM = "form"
    MULTIPART = "multipart"


def _is_body_digest(value: object) -> TypeGuard[BodyDigest]:
    if is_sensitive_value(value):
        return value.payload_type is Sha256Digest
    return type(value) is Sha256Digest


def _digest_origins(digest: BodyDigest | None) -> tuple[TaintOriginId, ...]:
    if digest is not None and is_sensitive_value(digest):
        return digest.origins
    return ()


def effective_request_taint_origins(
    url: AbsoluteHttpUrl,
    headers: RequestHeaders,
    cookies: RequestCookies,
    body: RequestBody,
) -> tuple[TaintOriginId, ...]:
    """Return every sealed value lineage contributing to an effective outbound request."""
    return request_envelope_taint_origins(
        url,
        headers,
        cookies,
        request_body_taint_origins(body),
    )


def request_envelope_taint_origins(
    url: AbsoluteHttpUrl,
    headers: RequestHeaders,
    cookies: RequestCookies,
    body_origins: tuple[TaintOriginId, ...],
) -> tuple[TaintOriginId, ...]:
    """Union request-envelope lineage with value-free body lineage at final boundaries."""
    if type(url) is not AbsoluteHttpUrl:
        raise TypeError("request lineage URL must be AbsoluteHttpUrl")
    if type(headers) is not RequestHeaders:
        raise TypeError("request lineage headers must be RequestHeaders")
    if type(cookies) is not RequestCookies:
        raise TypeError("request lineage cookies must be RequestCookies")
    if type(body_origins) is not tuple or any(
        type(origin) is not TaintOriginId for origin in body_origins
    ):
        raise TypeError("request body lineage must be an immutable tuple of TaintOriginId")
    header_origins = (
        origin
        for field in headers.fields
        if is_sensitive_value(field.value)
        for origin in field.value.origins
    )
    cookie_origins = (origin for cookie in cookies.cookies for origin in cookie.value.origins)
    return tuple(
        dict.fromkeys(
            (
                *url.taint_origins,
                *header_origins,
                *cookie_origins,
                *body_origins,
            )
        )
    )


def validate_effective_request_fingerprint(
    fingerprint: EffectiveRequestFingerprint,
    required_origins: tuple[TaintOriginId, ...],
) -> None:
    """Require public identity only for public requests and complete sealed lineage otherwise."""
    if not required_origins:
        if is_sensitive_value(fingerprint):
            raise ValueError("a public effective request requires a public fingerprint")
        return
    if not is_sensitive_value(fingerprint):
        raise ValueError("a sensitive effective request requires a protected fingerprint")
    if not frozenset(required_origins).issubset(fingerprint.origins):
        raise ValueError("effective request fingerprint does not cover all request sensitivity")


@final
class EffectiveRequestMaterializationIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """Opaque exact identity of an effective request, without retaining its input body."""

    environment: DefinitionRef
    logical_request_fingerprint: Fingerprint
    effective_request_fingerprint: EffectiveRequestFingerprint

    def __post_init__(self) -> None:
        """Require typed Environment and sensitivity-contained request fingerprints."""
        if type(self.environment) is not DefinitionRef:
            raise TypeError("materialization identity Environment must be DefinitionRef")
        if self.environment.identity.kind is not ResourceKind.ENVIRONMENT:
            raise ValueError("materialization identity must identify an Environment")
        if type(self.logical_request_fingerprint) is not Fingerprint:
            raise TypeError("materialization logical request identity must be Fingerprint")
        fingerprint = self.effective_request_fingerprint
        if type(fingerprint) is not Fingerprint and not is_sensitive_value(fingerprint):
            raise TypeError("effective request identity must be a protected Fingerprint")
        if is_sensitive_value(fingerprint) and fingerprint.payload_type is not Fingerprint:
            raise TypeError("sensitive effective request identity must contain Fingerprint")

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Return safe lineage carried by a sensitivity-contained request fingerprint."""
        if is_sensitive_value(self.effective_request_fingerprint):
            return self.effective_request_fingerprint.origins
        return ()


@final
class RequestBodyMaterializationInput(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free body shape and digest consumed by one materialization operation."""

    kind: RequestBodyKind
    digest: BodyDigest | None
    serialization: JsonSerialization | None = None
    media_type: MediaType | None = None
    multipart_boundary: MultipartBoundary | None = None

    def __post_init__(self) -> None:
        """Require only metadata applicable to the selected body shape."""
        _validate_input_metadata_types(self)
        _validate_input_metadata_shape(self)

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Return safe lineage of the sensitivity-contained input digest."""
        return _digest_origins(self.digest)


def _validate_input_metadata_types(value: RequestBodyMaterializationInput) -> None:
    """Validate exact types before applying body-kind metadata relationships."""
    if type(value.kind) is not RequestBodyKind:
        raise TypeError("materialization body kind must be RequestBodyKind")
    if value.digest is not None and not _is_body_digest(value.digest):
        raise TypeError("materialization input digest must preserve sensitivity")
    if value.serialization is not None and type(value.serialization) is not JsonSerialization:
        raise TypeError("materialization serialization must be JsonSerialization")
    if value.media_type is not None and type(value.media_type) is not MediaType:
        raise TypeError("materialization media type must be MediaType")
    if (
        value.multipart_boundary is not None
        and type(value.multipart_boundary) is not MultipartBoundary
    ):
        raise TypeError("materialization multipart boundary must be MultipartBoundary")


def _validate_input_metadata_shape(value: RequestBodyMaterializationInput) -> None:
    """Bind optional metadata exactly to the body shapes that define it."""
    if value.kind is RequestBodyKind.NONE:
        if any(
            item is not None
            for item in (
                value.digest,
                value.serialization,
                value.media_type,
                value.multipart_boundary,
            )
        ):
            raise ValueError("an absent body cannot carry materialization metadata")
        return
    if value.digest is None:
        raise ValueError("a present body requires an exact materialization digest")
    if (value.serialization is not None) is (value.kind is not RequestBodyKind.JSON):
        raise ValueError("only JSON body materialization carries serialization metadata")
    if value.media_type is not None and value.kind not in {
        RequestBodyKind.JSON,
        RequestBodyKind.TEXT,
        RequestBodyKind.BYTES,
    }:
        raise ValueError("this body kind cannot carry explicit media-type metadata")
    if (value.multipart_boundary is not None) is (value.kind is not RequestBodyKind.MULTIPART):
        raise ValueError("only multipart bodies require exact boundary metadata")


def describe_request_body(
    body: RequestBody,
    *,
    digest: BodyDigest | None,
) -> RequestBodyMaterializationInput:
    """Project a request body to value-free materialization metadata and exact digest."""
    if type(body) is NoBody:
        descriptor = RequestBodyMaterializationInput(
            kind=RequestBodyKind.NONE,
            digest=digest,
        )
    elif type(body) is JsonBody:
        descriptor = RequestBodyMaterializationInput(
            kind=RequestBodyKind.JSON,
            digest=digest,
            serialization=body.serialization,
            media_type=body.media_type,
        )
    elif type(body) is TextBody:
        descriptor = RequestBodyMaterializationInput(
            kind=RequestBodyKind.TEXT,
            digest=digest,
            media_type=body.media_type,
        )
    elif type(body) is BytesBody:
        descriptor = RequestBodyMaterializationInput(
            kind=RequestBodyKind.BYTES,
            digest=digest,
            media_type=body.media_type,
        )
    elif type(body) is FormBody:
        descriptor = RequestBodyMaterializationInput(
            kind=RequestBodyKind.FORM,
            digest=digest,
        )
    elif type(body) is MultipartBody:
        descriptor = RequestBodyMaterializationInput(
            kind=RequestBodyKind.MULTIPART,
            digest=digest,
            multipart_boundary=body.boundary,
        )
    else:
        raise TypeError("materialization descriptor requires an exact RequestBody")
    required = frozenset(request_body_taint_origins(body))
    if not required.issubset(descriptor.taint_origins):
        raise ValueError("materialization input digest does not cover body sensitivity origins")
    return descriptor


@final
class RequestMaterializationProof(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free proof binding an effective request identity to exact finalized bytes."""

    identity: EffectiveRequestMaterializationIdentity
    input_body: RequestBodyMaterializationInput
    body: FinalizedBody

    def __post_init__(self) -> None:
        """Bind input digest, output snapshot, multipart evidence, and sensitivity lineage."""
        _validate_proof_types(self)
        _validate_proof_body(self)


def _validate_proof_types(proof: RequestMaterializationProof) -> None:
    """Require exact proof components before cross-object validation."""
    if type(proof.identity) is not EffectiveRequestMaterializationIdentity:
        raise TypeError("materialization proof identity has the wrong type")
    if type(proof.input_body) is not RequestBodyMaterializationInput:
        raise TypeError("materialization proof input body has the wrong type")
    if type(proof.body) not in {NoMaterializedBody, MaterializedBody}:
        raise TypeError("materialization proof output must be a finalized body")


def _validate_proof_body(proof: RequestMaterializationProof) -> None:
    """Bind exact body digest, multipart evidence, and sensitivity lineage."""
    if proof.input_body.kind is RequestBodyKind.NONE:
        if type(proof.body) is not NoMaterializedBody:
            raise ValueError("an absent effective body cannot materialize entity bytes")
        return
    if type(proof.body) is not MaterializedBody:
        raise ValueError("a present effective body requires a complete materialized snapshot")
    if proof.input_body.digest != proof.body.source.digest:
        raise ValueError("materialized snapshot digest does not match its exact input")
    boundary = proof.input_body.multipart_boundary
    if proof.input_body.kind is RequestBodyKind.MULTIPART:
        if proof.body.multipart is None or proof.body.multipart.boundary != boundary:
            raise ValueError("multipart validation does not bind its exact input boundary")
    elif proof.body.multipart is not None:
        raise ValueError("non-multipart input cannot carry multipart boundary validation")
    required = frozenset(proof.input_body.taint_origins)
    if not required.issubset(proof.identity.taint_origins):
        raise ValueError("effective request identity does not cover body sensitivity origins")
