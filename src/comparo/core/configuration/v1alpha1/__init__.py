"""The strict ``comparo/v1alpha1`` external-language frontend."""

from __future__ import annotations

from comparo.core.configuration.v1alpha1.ast import V1Alpha1FrontendResult
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Metadata
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Resource
from comparo.core.configuration.v1alpha1.frontend import parse_v1alpha1_documents

__all__ = (
    "V1Alpha1FrontendResult",
    "V1Alpha1Metadata",
    "V1Alpha1Resource",
    "parse_v1alpha1_documents",
)
