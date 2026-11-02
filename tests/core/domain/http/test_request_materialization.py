"""Value-free request materialization identity and proof invariants."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.http.bodies import BodyDigest
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import JsonSerialization
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.request_materialization import EffectiveRequestMaterializationIdentity
from comparo.core.domain.http.request_materialization import RequestBodyKind
from comparo.core.domain.http.request_materialization import RequestBodyMaterializationInput
from comparo.core.domain.http.request_materialization import RequestMaterializationProof
from comparo.core.domain.http.request_materialization import describe_request_body
from comparo.core.domain.http.request_materialization import effective_request_taint_origins
from comparo.core.domain.http.request_materialization import request_envelope_taint_origins
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonString
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import finalized_request
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import multipart_body
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.factories import sensitive


def _identity() -> EffectiveRequestMaterializationIdentity:
    return finalized_request().materialization.identity


def _materialized() -> MaterializedBody:
    body, _ = multipart_body()
    return msgspec.structs.replace(body, multipart=None)


def _input(
    kind: RequestBodyKind = RequestBodyKind.TEXT,
    *,
    body_digest: BodyDigest | None = None,
) -> RequestBodyMaterializationInput:
    return RequestBodyMaterializationInput(
        kind=kind,
        digest=_materialized().source.digest if body_digest is None else body_digest,
        serialization=JsonSerialization.COMPARO_V1 if kind is RequestBodyKind.JSON else None,
        multipart_boundary=(
            MultipartBoundary(value="comparo-boundary")
            if kind is RequestBodyKind.MULTIPART
            else None
        ),
    )


def _effective_with_body(body: RequestBody) -> EffectiveRequest:
    base = finalized_request()
    return EffectiveRequest(
        environment=base.environment,
        logical_request_fingerprint=base.logical_request_fingerprint,
        method=base.method,
        url=base.url,
        headers=base.headers,
        cookies=base.cookies,
        body=body,
        policies=base.policies,
        provenance=base.provenance,
    )


def test_materialization_identity_rejects_untyped_or_wrong_resource_components() -> None:
    identity = _identity()
    with pytest.raises(TypeError, match="Environment"):
        msgspec.structs.replace(identity, environment=cast("DefinitionRef", object()))
    with pytest.raises(ValueError, match="identify an Environment"):
        msgspec.structs.replace(
            identity,
            environment=definition(ResourceKind.REQUEST, "request"),
        )
    with pytest.raises(TypeError, match="logical request identity"):
        msgspec.structs.replace(
            identity,
            logical_request_fingerprint=cast("Fingerprint", object()),
        )
    with pytest.raises(TypeError, match="protected Fingerprint"):
        msgspec.structs.replace(
            identity,
            effective_request_fingerprint=cast("Fingerprint", object()),
        )
    with pytest.raises(TypeError, match="contain Fingerprint"):
        msgspec.structs.replace(
            identity,
            effective_request_fingerprint=cast(
                "SensitiveValue[Fingerprint]",
                sensitive(BodyText(value="not-a-fingerprint")),
            ),
        )


def test_materialization_input_rejects_untyped_metadata() -> None:
    body_digest = digest()
    invalid: tuple[tuple[str, object], ...] = (
        ("kind", object()),
        ("digest", object()),
        ("serialization", object()),
        ("media_type", object()),
        ("multipart_boundary", object()),
    )
    base = RequestBodyMaterializationInput(
        kind=RequestBodyKind.JSON,
        digest=body_digest,
        serialization=JsonSerialization.COMPARO_V1,
    )
    for field, value in invalid:
        with pytest.raises(TypeError):
            msgspec.structs.replace(base, **{field: value})

    wrong_sensitive_digest = SensitiveValue(
        BodyText(value="not-a-digest"),
        origins=(TaintOriginId(value="wrong-digest"),),
    )
    with pytest.raises(TypeError, match="digest must preserve sensitivity"):
        msgspec.structs.replace(
            base,
            digest=cast("BodyDigest", wrong_sensitive_digest),
        )


def test_materialization_input_metadata_is_owned_by_exact_body_kind() -> None:
    body_digest = digest()
    media = MediaType(type="application", subtype="json")
    with pytest.raises(ValueError, match="absent body"):
        RequestBodyMaterializationInput(
            kind=RequestBodyKind.NONE,
            digest=None,
            media_type=media,
        )
    with pytest.raises(ValueError, match="present body requires"):
        RequestBodyMaterializationInput(kind=RequestBodyKind.TEXT, digest=None)
    with pytest.raises(ValueError, match="only JSON"):
        RequestBodyMaterializationInput(
            kind=RequestBodyKind.JSON,
            digest=body_digest,
        )
    with pytest.raises(ValueError, match="only JSON"):
        RequestBodyMaterializationInput(
            kind=RequestBodyKind.TEXT,
            digest=body_digest,
            serialization=JsonSerialization.COMPARO_V1,
        )
    with pytest.raises(ValueError, match="cannot carry explicit media"):
        RequestBodyMaterializationInput(
            kind=RequestBodyKind.FORM,
            digest=body_digest,
            media_type=media,
        )
    with pytest.raises(ValueError, match="only multipart"):
        RequestBodyMaterializationInput(
            kind=RequestBodyKind.MULTIPART,
            digest=body_digest,
        )
    with pytest.raises(ValueError, match="only multipart"):
        RequestBodyMaterializationInput(
            kind=RequestBodyKind.TEXT,
            digest=body_digest,
            multipart_boundary=MultipartBoundary(value="boundary"),
        )


def test_body_descriptors_cover_json_form_and_invalid_body_dispatch() -> None:
    body_digest = digest()
    media = MediaType(type="application", subtype="json")
    json_descriptor = describe_request_body(
        JsonBody(value=JsonString(value="value"), media_type=media),
        digest=body_digest,
    )
    form_descriptor = describe_request_body(FormBody(fields=()), digest=body_digest)
    bytes_descriptor = describe_request_body(
        BytesBody(value=_materialized().source),
        digest=body_digest,
    )

    assert json_descriptor.kind is RequestBodyKind.JSON
    assert json_descriptor.serialization is JsonSerialization.COMPARO_V1
    assert json_descriptor.media_type is media
    assert form_descriptor.kind is RequestBodyKind.FORM
    assert bytes_descriptor.kind is RequestBodyKind.BYTES
    with pytest.raises(TypeError, match="exact RequestBody"):
        describe_request_body(cast("RequestBody", object()), digest=body_digest)


def test_materialization_proof_rejects_untyped_and_cross_body_components() -> None:
    identity = _identity()
    body = _materialized()
    input_body = _input(body_digest=body.source.digest)
    proof = RequestMaterializationProof(identity=identity, input_body=input_body, body=body)

    with pytest.raises(TypeError, match="identity has the wrong type"):
        msgspec.structs.replace(
            proof,
            identity=cast("EffectiveRequestMaterializationIdentity", object()),
        )
    with pytest.raises(TypeError, match="input body has the wrong type"):
        msgspec.structs.replace(
            proof,
            input_body=cast("RequestBodyMaterializationInput", object()),
        )
    with pytest.raises(TypeError, match="output must be a finalized body"):
        msgspec.structs.replace(proof, body=cast("FinalizedBody", object()))
    with pytest.raises(ValueError, match="absent effective body"):
        RequestMaterializationProof(
            identity=identity,
            input_body=RequestBodyMaterializationInput(
                kind=RequestBodyKind.NONE,
                digest=None,
            ),
            body=body,
        )
    with pytest.raises(ValueError, match="present effective body"):
        RequestMaterializationProof(
            identity=identity,
            input_body=input_body,
            body=NoMaterializedBody(),
        )
    with pytest.raises(ValueError, match="digest does not match"):
        RequestMaterializationProof(
            identity=identity,
            input_body=_input(body_digest=digest("b")),
            body=body,
        )


def test_materialization_proof_requires_identity_to_cover_sensitive_digest() -> None:
    body = _materialized()
    protected_digest = sensitive(cast("Sha256Digest", body.source.digest))
    protected_body = msgspec.structs.replace(
        body,
        source=msgspec.structs.replace(body.source, digest=protected_digest),
    )
    protected_input = _input(body_digest=protected_digest)
    with pytest.raises(ValueError, match="identity does not cover"):
        RequestMaterializationProof(
            identity=_identity(),
            input_body=protected_input,
            body=protected_body,
        )


def _is_reachable(root: object, target: object) -> bool:
    """Walk immutable carriers by identity without interpreting or rendering payloads."""
    pending = [root]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current is target:
            return True
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)
        if isinstance(current, msgspec.Struct):
            pending.extend(getattr(current, field) for field in current.__struct_fields__)
        elif type(current) is tuple:
            pending.extend(current)
    return False


def test_large_inline_body_is_not_reachable_from_proof_or_finalized_request() -> None:
    """Proofs retain an opaque digest/shape, never the potentially large input body value."""
    inline = BodyText(value="x" * 100_000)
    effective = _effective_with_body(TextBody(value=inline))
    output = _materialized()
    proof = prove_request_materialization(
        effective,
        output,
        effective_request_fingerprint=fingerprint("9"),
    )
    finalized = FinalizedWireRequest(
        environment=effective.environment,
        logical_request_fingerprint=effective.logical_request_fingerprint,
        method=effective.method,
        url=effective.url,
        headers=effective.headers,
        cookies=effective.cookies,
        body=output,
        materialization=proof,
        policies=effective.policies,
        provenance=effective.provenance,
    )

    assert not _is_reachable(proof, inline)
    assert not _is_reachable(finalized, inline)
    assert proof.input_body.digest == output.source.digest


@pytest.mark.parametrize("carrier", ["path", "query", "header", "cookie", "body"])
def test_effective_request_fingerprint_covers_every_sensitive_value_carrier(
    carrier: str,
) -> None:
    """No sensitive URL, header, cookie, or body value may receive a public identity."""
    origin = TaintOriginId(value=f"{carrier}-origin")
    effective = _effective_with_body(TextBody(value=BodyText(value="public")))
    output: FinalizedBody = _materialized()
    if carrier == "path":
        effective = msgspec.structs.replace(
            effective,
            url=msgspec.structs.replace(
                effective.url,
                path=UrlPath(
                    segments=(
                        SensitiveValue(
                            DecodedPathSegment(value="secret"),
                            origins=(origin,),
                        ),
                    )
                ),
            ),
        )
    elif carrier == "query":
        effective = msgspec.structs.replace(
            effective,
            url=msgspec.structs.replace(
                effective.url,
                query=QueryParameters(
                    parameters=(
                        QueryParameter(
                            name=QueryName(value="token"),
                            value=SensitiveValue(QueryText(value="secret"), origins=(origin,)),
                            source=QuerySource.AUTHENTICATION,
                            provenance=provenance(),
                        ),
                    )
                ),
            ),
        )
    elif carrier == "header":
        effective = msgspec.structs.replace(
            effective,
            headers=RequestHeaders(
                fields=(
                    HeaderField(
                        name=HeaderName(value="x-api-key"),
                        value=SensitiveValue(HeaderText(value="secret"), origins=(origin,)),
                        source=HeaderSource.AUTHENTICATION,
                        provenance=provenance(),
                    ),
                )
            ),
        )
    elif carrier == "cookie":
        effective = msgspec.structs.replace(
            effective,
            cookies=RequestCookies(
                cookies=(
                    RequestCookie(
                        name=CookieName(value="session"),
                        value=SensitiveValue(CookieText(value="secret"), origins=(origin,)),
                        source=CookieSource.EXPLICIT,
                        provenance=provenance(),
                    ),
                )
            ),
        )
    else:
        effective = msgspec.structs.replace(
            effective,
            body=TextBody(value=SensitiveValue(BodyText(value="secret"), origins=(origin,))),
        )
        materialized = _materialized()
        output = msgspec.structs.replace(
            materialized,
            source=msgspec.structs.replace(
                materialized.source,
                digest=SensitiveValue(
                    cast("Sha256Digest", materialized.source.digest),
                    origins=(origin,),
                ),
            ),
        )

    assert effective_request_taint_origins(
        effective.url,
        effective.headers,
        effective.cookies,
        effective.body,
    ) == (origin,)
    with pytest.raises(ValueError, match="requires a protected fingerprint"):
        prove_request_materialization(
            effective,
            output,
            effective_request_fingerprint=fingerprint("9"),
        )
    proof = prove_request_materialization(
        effective,
        output,
        effective_request_fingerprint=SensitiveValue(fingerprint("9"), origins=(origin,)),
    )
    assert proof.identity.taint_origins == (origin,)


def test_effective_request_fingerprint_rejects_overtaint_and_missing_lineage() -> None:
    public = _effective_with_body(TextBody(value=BodyText(value="public")))
    decorative = TaintOriginId(value="decorative")
    with pytest.raises(ValueError, match="public effective request requires a public"):
        prove_request_materialization(
            public,
            _materialized(),
            effective_request_fingerprint=SensitiveValue(
                fingerprint("9"),
                origins=(decorative,),
            ),
        )

    required = TaintOriginId(value="required")
    sensitive_effective = msgspec.structs.replace(
        public,
        headers=RequestHeaders(
            fields=(
                HeaderField(
                    name=HeaderName(value="x-api-key"),
                    value=SensitiveValue(HeaderText(value="secret"), origins=(required,)),
                    source=HeaderSource.AUTHENTICATION,
                    provenance=provenance(),
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="does not cover all request sensitivity"):
        prove_request_materialization(
            sensitive_effective,
            _materialized(),
            effective_request_fingerprint=SensitiveValue(
                fingerprint("9"),
                origins=(decorative,),
            ),
        )


def test_effective_request_lineage_rejects_untyped_top_level_carriers() -> None:
    effective = _effective_with_body(TextBody(value=BodyText(value="public")))
    with pytest.raises(TypeError, match="lineage URL"):
        effective_request_taint_origins(
            cast("AbsoluteHttpUrl", object()),
            effective.headers,
            effective.cookies,
            effective.body,
        )
    with pytest.raises(TypeError, match="lineage headers"):
        effective_request_taint_origins(
            effective.url,
            cast("RequestHeaders", object()),
            effective.cookies,
            effective.body,
        )
    with pytest.raises(TypeError, match="lineage cookies"):
        effective_request_taint_origins(
            effective.url,
            effective.headers,
            cast("RequestCookies", object()),
            effective.body,
        )
    with pytest.raises(TypeError, match="request body lineage"):
        request_envelope_taint_origins(
            effective.url,
            effective.headers,
            effective.cookies,
            cast("tuple[TaintOriginId, ...]", (object(),)),
        )


def _sensitive_finalized_envelope(
    request: FinalizedWireRequest,
    carrier: str,
    origin: TaintOriginId,
) -> tuple[AbsoluteHttpUrl, RequestHeaders, RequestCookies]:
    url = request.url
    headers = request.headers
    cookies = request.cookies
    if carrier == "path":
        url = msgspec.structs.replace(
            url,
            path=UrlPath(
                segments=(
                    SensitiveValue(
                        DecodedPathSegment(value="secret"),
                        origins=(origin,),
                    ),
                )
            ),
        )
    elif carrier == "query":
        url = msgspec.structs.replace(
            url,
            query=QueryParameters(
                parameters=(
                    QueryParameter(
                        name=QueryName(value="token"),
                        value=SensitiveValue(QueryText(value="secret"), origins=(origin,)),
                        source=QuerySource.AUTHENTICATION,
                        provenance=provenance(),
                    ),
                )
            ),
        )
    elif carrier == "header":
        headers = RequestHeaders(
            fields=(
                HeaderField(
                    name=HeaderName(value="x-api-key"),
                    value=SensitiveValue(HeaderText(value="secret"), origins=(origin,)),
                    source=HeaderSource.AUTHENTICATION,
                    provenance=provenance(),
                ),
            )
        )
    else:
        cookies = RequestCookies(
            cookies=(
                RequestCookie(
                    name=CookieName(value="session"),
                    value=SensitiveValue(CookieText(value="secret"), origins=(origin,)),
                    source=CookieSource.EXPLICIT,
                    provenance=provenance(),
                ),
            )
        )
    return url, headers, cookies


@pytest.mark.parametrize("carrier", ["path", "query", "header", "cookie"])
def test_finalized_request_direct_construction_enforces_envelope_lineage(
    carrier: str,
) -> None:
    """Skipping the proof factory cannot attach public identity to a sealed envelope."""
    request = finalized_request()
    origin = TaintOriginId(value=f"direct-{carrier}")
    url, headers, cookies = _sensitive_finalized_envelope(request, carrier, origin)

    with pytest.raises(ValueError, match="requires a protected fingerprint"):
        msgspec.structs.replace(
            request,
            url=url,
            headers=headers,
            cookies=cookies,
        )

    protected_identity = msgspec.structs.replace(
        request.materialization.identity,
        effective_request_fingerprint=SensitiveValue(
            fingerprint("9"),
            origins=(origin,),
        ),
    )
    protected_proof = msgspec.structs.replace(
        request.materialization,
        identity=protected_identity,
    )
    finalized = msgspec.structs.replace(
        request,
        url=url,
        headers=headers,
        cookies=cookies,
        materialization=protected_proof,
    )

    assert finalized.materialization.identity.taint_origins == (origin,)


def test_finalized_request_direct_construction_rejects_wrong_or_decorative_lineage() -> None:
    request = finalized_request()
    required = TaintOriginId(value="direct-required")
    decorative = TaintOriginId(value="direct-decorative")
    url, headers, cookies = _sensitive_finalized_envelope(request, "header", required)
    decorative_proof = msgspec.structs.replace(
        request.materialization,
        identity=msgspec.structs.replace(
            request.materialization.identity,
            effective_request_fingerprint=SensitiveValue(
                fingerprint("9"),
                origins=(decorative,),
            ),
        ),
    )

    with pytest.raises(ValueError, match="does not cover all request sensitivity"):
        msgspec.structs.replace(
            request,
            url=url,
            headers=headers,
            cookies=cookies,
            materialization=decorative_proof,
        )
    with pytest.raises(ValueError, match="public effective request requires a public"):
        msgspec.structs.replace(request, materialization=decorative_proof)
