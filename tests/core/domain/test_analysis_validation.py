"""Validation tests for external-analysis providers, inputs, and budgets."""

from typing import cast

import msgspec
import pytest

from comparo.core.domain.analysis import AnalysisInput
from comparo.core.domain.analysis import AnalysisProvider
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSchema
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.analysis_factories import _budget
from tests.core.domain.analysis_factories import _destination
from tests.core.domain.analysis_factories import _fingerprint
from tests.core.domain.analysis_factories import _input
from tests.core.domain.analysis_factories import _request
from tests.core.domain.analysis_factories import _sensitive_fingerprint


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider", "Bad Provider", "lowercase namespaced token"),
        ("provider_version", " ", "provider version"),
        ("model", "", "model"),
        ("model_version", " ", "model version"),
    ],
)
def test_analysis_provider_rejects_invalid_identity_parts(
    field: str,
    value: str,
    message: str,
) -> None:
    fields: dict[str, object] = {
        "provider": "example.judge",
        "provider_version": "1",
        "model": "judge",
        "model_version": "1",
        "destination": _destination(),
    }
    fields[field] = value
    with pytest.raises(ValueError, match=message):
        AnalysisProvider(**fields)  # type: ignore[arg-type]


def test_analysis_provider_requires_destination_type_and_kind() -> None:
    with pytest.raises(TypeError, match="NamedCapabilityTarget"):
        AnalysisProvider(
            provider="example.judge",
            provider_version="1",
            model="judge",
            model_version="1",
            destination=cast("NamedCapabilityTarget", object()),
        )
    with pytest.raises(ValueError, match="destination target kind"):
        AnalysisProvider(
            provider="example.judge",
            provider_version="1",
            model="judge",
            model_version="1",
            destination=NamedCapabilityTarget(
                kind=CapabilityTargetKind.PROVIDER,
                identifier="example",
            ),
        )


def test_analysis_schema_requires_exact_identity_version_and_fingerprint() -> None:
    with pytest.raises(ValueError, match="lowercase namespaced token"):
        AnalysisSchema(identifier="Bad schema", version="1", fingerprint=_fingerprint())
    with pytest.raises(ValueError, match="version"):
        AnalysisSchema(identifier="comparo.result", version=" ", fingerprint=_fingerprint())
    with pytest.raises(TypeError, match="Fingerprint"):
        AnalysisSchema(
            identifier="comparo.result",
            version="1",
            fingerprint=cast("Fingerprint", object()),
        )


def test_analysis_input_rejects_every_invalid_component() -> None:
    with pytest.raises(TypeError, match="source"):
        AnalysisInput(
            source=cast("BodyId", object()),
            digest=_fingerprint(),
            bytes=ByteCount(value=1),
            sensitivity=Sensitivity.PUBLIC,
        )
    with pytest.raises(TypeError, match="digest"):
        AnalysisInput(
            source=_input().source,
            digest=cast("Fingerprint", object()),
            bytes=ByteCount(value=1),
            sensitivity=Sensitivity.PUBLIC,
        )
    with pytest.raises(TypeError, match="digest"):
        AnalysisInput(
            source=_input().source,
            digest=cast(
                "SensitiveValue[Fingerprint]",
                SensitiveValue(
                    Count(value=1),
                    origins=(TaintOriginId(value="wrong/type"),),
                ),
            ),
            bytes=ByteCount(value=1),
            sensitivity=Sensitivity.SENSITIVE,
        )
    with pytest.raises(TypeError, match="bytes"):
        AnalysisInput(
            source=_input().source,
            digest=_fingerprint(),
            bytes=cast("ByteCount", object()),
            sensitivity=Sensitivity.PUBLIC,
        )
    with pytest.raises(TypeError, match="sensitivity"):
        AnalysisInput(
            source=_input().source,
            digest=_fingerprint(),
            bytes=ByteCount(value=1),
            sensitivity=cast("Sensitivity", object()),
        )
    with pytest.raises(ValueError, match="containment"):
        AnalysisInput(
            source=_input().source,
            digest=_sensitive_fingerprint(),
            bytes=ByteCount(value=1),
            sensitivity=Sensitivity.PUBLIC,
        )
    with pytest.raises(ValueError, match="media type"):
        AnalysisInput(
            source=_input().source,
            digest=_fingerprint(),
            bytes=ByteCount(value=1),
            sensitivity=Sensitivity.PUBLIC,
            media_type=" ",
        )


def test_analysis_request_rejects_untyped_components_and_containment_mismatch() -> None:
    request = _request()
    fields = {
        "analysis_id": request.analysis_id,
        "provider": request.provider,
        "inputs": request.inputs,
        "output_schema": request.output_schema,
        "budgets": request.budgets,
        "timeout": request.timeout,
        "fingerprint": request.fingerprint,
    }
    for field, message in (
        ("analysis_id", "AnalysisId"),
        ("provider", "AnalysisProvider"),
        ("output_schema", "AnalysisSchema"),
        ("timeout", "Duration"),
    ):
        invalid = fields | {field: object()}
        with pytest.raises(TypeError, match=message):
            AnalysisRequest(**invalid)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable tuple"):
        AnalysisRequest(**(fields | {"inputs": list(request.inputs)}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AnalysisInput"):
        AnalysisRequest(**(fields | {"inputs": (object(),)}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable tuple"):
        AnalysisRequest(**(fields | {"budgets": list(request.budgets)}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ResourceBudget"):
        AnalysisRequest(**(fields | {"budgets": (object(),)}))  # type: ignore[arg-type]
    misplaced_deadline = _budget(
        BudgetResource.DEADLINE,
        Duration(nanoseconds=100),
        "analysis.deadline",
        scope_kind=BudgetScopeKind.BODY,
        stage=BudgetStage.HTTP,
    )
    with pytest.raises(ValueError, match="external-capability scope"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=request.provider,
            inputs=request.inputs,
            output_schema=request.output_schema,
            budgets=(*request.budgets[:2], misplaced_deadline, request.budgets[3]),
            timeout=request.timeout,
            fingerprint=request.fingerprint,
        )
    with pytest.raises(TypeError, match="fingerprint"):
        AnalysisRequest(**(fields | {"fingerprint": object()}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="fingerprint"):
        AnalysisRequest(
            **{  # type: ignore[arg-type]
                **fields,
                "fingerprint": cast(
                    "SensitiveValue[Fingerprint]",
                    SensitiveValue(
                        Count(value=1),
                        origins=(TaintOriginId(value="wrong/type"),),
                    ),
                ),
            }
        )
    with pytest.raises(ValueError, match="containment"):
        AnalysisRequest(
            **(fields | {"fingerprint": _fingerprint()})  # type: ignore[arg-type]
        )
    public_request = _request(sensitive=False)
    public_fields = {
        "analysis_id": public_request.analysis_id,
        "provider": public_request.provider,
        "inputs": public_request.inputs,
        "output_schema": public_request.output_schema,
        "budgets": public_request.budgets,
        "timeout": public_request.timeout,
        "fingerprint": _sensitive_fingerprint(),
    }
    with pytest.raises(ValueError, match="containment"):
        AnalysisRequest(**public_fields)  # type: ignore[arg-type]


def test_analysis_requires_exact_budget_coordinates_and_effective_limits() -> None:
    request = _request()
    fields = {
        "analysis_id": request.analysis_id,
        "provider": request.provider,
        "inputs": request.inputs,
        "output_schema": request.output_schema,
        "budgets": request.budgets,
        "timeout": request.timeout,
        "fingerprint": request.fingerprint,
    }
    with pytest.raises(ValueError, match="each required dimension exactly once"):
        AnalysisRequest(
            **(fields | {"budgets": (request.budgets[0], *request.budgets[0:3])})  # type: ignore[arg-type]
        )
    wrong_operation = msgspec.structs.replace(
        request.budgets[0],
        operation=BudgetOperation(value="analysis.wrong"),
    )
    with pytest.raises(ValueError, match="canonical operation"):
        AnalysisRequest(
            **(fields | {"budgets": (wrong_operation, *request.budgets[1:])})  # type: ignore[arg-type]
        )
    low_input = _budget(
        BudgetResource.EXTERNAL_INPUT_BYTES,
        ByteCount(value=11),
        "analysis.input",
    )
    with pytest.raises(ValueError, match="input bytes exceed"):
        AnalysisRequest(
            **(fields | {"budgets": (low_input, *request.budgets[1:])})  # type: ignore[arg-type]
        )
    short_deadline = _budget(
        BudgetResource.DEADLINE,
        Duration(nanoseconds=99),
        "analysis.deadline",
    )
    with pytest.raises(ValueError, match="timeout exceeds"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=request.provider,
            inputs=request.inputs,
            output_schema=request.output_schema,
            budgets=(*request.budgets[:2], short_deadline, request.budgets[3]),
            timeout=request.timeout,
            fingerprint=request.fingerprint,
        )
