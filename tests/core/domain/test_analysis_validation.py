"""Validation tests for external-analysis providers, inputs, and budgets."""

from typing import cast

import msgspec
import pytest

from comparo.core.domain.analysis import AnalysisInput
from comparo.core.domain.analysis import AnalysisProvider
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSchema
from comparo.core.domain.analysis_types import AnalysisContractVersion
from comparo.core.domain.analysis_types import AnalysisModelId
from comparo.core.domain.analysis_types import AnalysisProviderId
from comparo.core.domain.analysis_types import AnalysisSchemaId
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
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
from tests.core.domain.analysis_factories import _analysis_id
from tests.core.domain.analysis_factories import _analysis_scope
from tests.core.domain.analysis_factories import _budget
from tests.core.domain.analysis_factories import _destination
from tests.core.domain.analysis_factories import _fingerprint
from tests.core.domain.analysis_factories import _input
from tests.core.domain.analysis_factories import _request
from tests.core.domain.analysis_factories import _run_scope
from tests.core.domain.analysis_factories import _sensitive_fingerprint


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("provider", "AnalysisProviderId"),
        ("provider_version", "AnalysisContractVersion"),
        ("model", "AnalysisModelId"),
        ("model_version", "AnalysisContractVersion"),
    ],
)
def test_analysis_provider_rejects_invalid_identity_parts(
    field: str,
    message: str,
) -> None:
    fields: dict[str, object] = {
        "provider": AnalysisProviderId(value="example.judge"),
        "provider_version": AnalysisContractVersion(value="1"),
        "model": AnalysisModelId(value="judge"),
        "model_version": AnalysisContractVersion(value="1"),
        "destination": _destination(),
    }
    fields[field] = "unsafe\nmetadata"
    with pytest.raises(TypeError, match=message):
        AnalysisProvider(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("constructor", "unsafe"),
    [
        (AnalysisProviderId, "Bad Provider"),
        (AnalysisProviderId, "provider\nAuthorization"),
        (AnalysisProviderId, "a" * 128),
        (AnalysisProviderId, 1),
        (AnalysisModelId, "model=secret"),
        (AnalysisModelId, "model\nheader"),
        (AnalysisModelId, "a" * 129),
        (AnalysisModelId, 1),
        (AnalysisContractVersion, "version 1"),
        (AnalysisContractVersion, "version\nheader"),
        (AnalysisContractVersion, "a" * 65),
        (AnalysisContractVersion, 1),
        (AnalysisSchemaId, "Bad schema"),
        (AnalysisSchemaId, "a" * 128),
        (AnalysisSchemaId, 1),
    ],
)
def test_analysis_metadata_types_reject_payload_and_control_text(
    constructor: type[
        AnalysisProviderId | AnalysisModelId | AnalysisContractVersion | AnalysisSchemaId
    ],
    unsafe: object,
) -> None:
    with pytest.raises(ValueError, match="bounded"):
        constructor(value=unsafe)  # type: ignore[arg-type]


def test_analysis_provider_requires_destination_type_and_kind() -> None:
    with pytest.raises(TypeError, match="NamedCapabilityTarget"):
        AnalysisProvider(
            provider=AnalysisProviderId(value="example.judge"),
            provider_version=AnalysisContractVersion(value="1"),
            model=AnalysisModelId(value="judge"),
            model_version=AnalysisContractVersion(value="1"),
            destination=cast("NamedCapabilityTarget", object()),
        )
    with pytest.raises(ValueError, match="destination target kind"):
        AnalysisProvider(
            provider=AnalysisProviderId(value="example.judge"),
            provider_version=AnalysisContractVersion(value="1"),
            model=AnalysisModelId(value="judge"),
            model_version=AnalysisContractVersion(value="1"),
            destination=NamedCapabilityTarget(
                kind=CapabilityTargetKind.PROVIDER,
                identifier="example",
            ),
        )


def test_analysis_schema_requires_exact_identity_version_and_fingerprint() -> None:
    with pytest.raises(TypeError, match="AnalysisSchemaId"):
        AnalysisSchema(
            identifier="comparo.result",  # type: ignore[arg-type]
            version=AnalysisContractVersion(value="1"),
            fingerprint=_fingerprint(),
        )
    with pytest.raises(TypeError, match="AnalysisContractVersion"):
        AnalysisSchema(
            identifier=AnalysisSchemaId(value="comparo.result"),
            version="1",  # type: ignore[arg-type]
            fingerprint=_fingerprint(),
        )
    with pytest.raises(TypeError, match="Fingerprint"):
        AnalysisSchema(
            identifier=AnalysisSchemaId(value="comparo.result"),
            version=AnalysisContractVersion(value="1"),
            fingerprint=cast("Fingerprint", object()),
        )
    with pytest.raises(TypeError, match="empty-output permission"):
        AnalysisSchema(
            identifier=AnalysisSchemaId(value="comparo.result"),
            version=AnalysisContractVersion(value="1"),
            fingerprint=_fingerprint(),
            allows_empty_output=cast("bool", 1),
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
    with pytest.raises(TypeError, match="media type"):
        AnalysisInput(
            source=_input().source,
            digest=_fingerprint(),
            bytes=ByteCount(value=1),
            sensitivity=Sensitivity.PUBLIC,
            media_type="application/json",  # type: ignore[arg-type]
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
        scope_kind=BudgetScopeKind.RUN,
        stage=BudgetStage.HTTP,
        scope=_run_scope(),
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


def test_all_analysis_budgets_bind_one_exact_run_and_analysis_occurrence() -> None:
    request = _request()
    assert all(budget.scope == request.budgets[0].scope for budget in request.budgets)

    foreign_analysis = msgspec.structs.replace(
        request.budgets[0],
        scope=_analysis_scope(analysis_id=_analysis_id(2)),
    )
    with pytest.raises(ValueError, match="match the AnalysisRequest analysis_id"):
        msgspec.structs.replace(
            request,
            budgets=(foreign_analysis, *request.budgets[1:]),
        )

    foreign_run = msgspec.structs.replace(
        request.budgets[1],
        scope=_analysis_scope(
            analysis_id=request.analysis_id,
            run=_run_scope(11),
        ),
    )
    with pytest.raises(ValueError, match="one identical run and analysis scope"):
        msgspec.structs.replace(
            request,
            budgets=(request.budgets[0], foreign_run, *request.budgets[2:]),
        )


def test_analysis_authorization_budgets_must_start_unused() -> None:
    request = _request()
    started = msgspec.structs.replace(
        request.budgets[0],
        usage=BudgetUsage(
            consumed=ByteCount(value=1),
            in_use=ByteCount(value=0),
            high_water=ByteCount(value=1),
        ),
    )
    with pytest.raises(ValueError, match="start with zero usage"):
        msgspec.structs.replace(
            request,
            budgets=(started, *request.budgets[1:]),
        )
