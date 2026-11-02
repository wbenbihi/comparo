"""Request-body materialization sensitivity-lineage regressions."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import FormField
from comparo.core.domain.http.bodies import FormName
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartDispositionName
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.body_lineage import request_body_taint_origins
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import multipart_body
from tests.core.domain.http.factories import sensitive


def test_request_body_lineage_covers_every_initial_body_shape() -> None:
    """Collect nested sealed values once while public inputs contribute no lineage."""
    origin = TaintOriginId(value="validation-test")
    materialized, validation = multipart_body()
    sensitive_source = type(materialized.source)(
        source_id=materialized.source.source_id,
        byte_count=materialized.source.byte_count,
        digest=sensitive(digest("f")),
        provenance=materialized.source.provenance,
    )
    assert request_body_taint_origins(NoBody()) == ()
    assert request_body_taint_origins(TextBody(value=BodyText(value="public"))) == ()
    assert request_body_taint_origins(JsonBody(value=sensitive(JsonString(value="secret")))) == (
        origin,
    )
    assert request_body_taint_origins(BytesBody(value=sensitive_source)) == (origin,)

    form = FormBody(
        fields=(
            FormField(
                name=FormName(value="secret"),
                value=sensitive(BodyText(value="value")),
            ),
            FormField(name=FormName(value="public"), value=BodyText(value="value")),
        )
    )
    assert request_body_taint_origins(form) == (origin,)

    multipart = MultipartBody(
        boundary=validation.boundary,
        parts=(
            MultipartTextPart(
                name=MultipartDispositionName(value="text"),
                value=sensitive(BodyText(value="secret")),
            ),
            MultipartFilePart(
                name=MultipartDispositionName(value="file"),
                filename=sensitive(MultipartDispositionFilename(value="secret.bin")),
                value=BytesValue(value=b"public"),
                media_type=MediaType(type="application", subtype="octet-stream"),
            ),
        ),
    )
    assert request_body_taint_origins(multipart) == (origin,)

    with pytest.raises(TypeError, match="exact RequestBody"):
        request_body_taint_origins(cast("RequestBody", object()))
