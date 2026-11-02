"""Run-scoped value resolution with explicit effects, caching, and taint retention."""

from comparo.core.resolution.context import RunContext as RunContext
from comparo.core.resolution.control import RunCancellation as RunCancellation
from comparo.core.resolution.generation import DeterministicGenerator as DeterministicGenerator
from comparo.core.resolution.models import CellInputs as CellInputs
from comparo.core.resolution.models import EnvironmentSnapshot as EnvironmentSnapshot
from comparo.core.resolution.models import EnvironmentSnapshotEntry as EnvironmentSnapshotEntry
from comparo.core.resolution.models import ExternalProviderSource as ExternalProviderSource
from comparo.core.resolution.models import GenerationOperation as GenerationOperation
from comparo.core.resolution.models import GenerationSpec as GenerationSpec
from comparo.core.resolution.models import ProviderBlocked as ProviderBlocked
from comparo.core.resolution.models import ProviderError as ProviderError
from comparo.core.resolution.models import ProviderOutcome as ProviderOutcome
from comparo.core.resolution.models import ProviderPresent as ProviderPresent
from comparo.core.resolution.models import ProviderUnavailable as ProviderUnavailable
from comparo.core.resolution.models import ResolutionTarget as ResolutionTarget
from comparo.core.resolution.models import ResolvedForWire as ResolvedForWire
from comparo.core.resolution.models import (
    SanitizationPatternDisposition as SanitizationPatternDisposition,
)
from comparo.core.resolution.models import SanitizationSnapshot as SanitizationSnapshot
from comparo.core.resolution.models import WireResolution as WireResolution
from comparo.core.resolution.models import YamlDecodeRequest as YamlDecodeRequest
from comparo.core.resolution.snapshots import cell_inputs as cell_inputs
from comparo.core.resolution.views import MASKED_VALUE_MARKER as MASKED_VALUE_MARKER
from comparo.core.resolution.views import LiveValueResolver as LiveValueResolver
from comparo.core.resolution.views import MaskedPresent as MaskedPresent
from comparo.core.resolution.views import MaskedResolutionResult as MaskedResolutionResult
from comparo.core.resolution.views import MaskedSensitiveValue as MaskedSensitiveValue
from comparo.core.resolution.views import MaskedValueResolver as MaskedValueResolver
from comparo.core.resolution.views import mask_resolution as mask_resolution
