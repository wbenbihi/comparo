"""Selected values, safe view provenance, and shared result invariants."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.domain.http.response_bodies import MediaTypeMetadata
from comparo.core.domain.http.response_bodies import is_media_type_metadata
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.selectors.contracts import contract_accepts_runtime_kind
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.locations import location_environment_execution_id
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import require_location_matches_selector
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import is_selected_value
from comparo.core.domain.selectors.values import is_selected_value_type
from comparo.core.domain.selectors.values import selected_value_kind_type
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value


@final
class SelectionMatch(msgspec.Struct, frozen=True, kw_only=True):
    """One selected value at one concrete typed source location."""

    value: SelectedValue | SensitiveValue[SelectedValue]
    location: ConcreteLocation

    def __post_init__(self) -> None:
        """Require closed typed value and location unions."""
        if is_sensitive_value(self.value) and not is_selected_value_type(self.value.payload_type):
            raise TypeError("sensitive selection payload has an unsupported type")
        if not is_sensitive_value(self.value) and not is_selected_value(self.value):
            raise TypeError("selection match value has an unsupported type")
        if not is_concrete_location(self.location):
            raise TypeError("selection match location has an unsupported type")
        _require_handle_identity(self)

    @property
    def sensitivity(self) -> Sensitivity:
        """Derive sensitivity from containment; callers cannot mislabel a secret public."""
        return Sensitivity.SENSITIVE if is_sensitive_value(self.value) else Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Expose safe lineage identifiers without revealing selected payload data."""
        return self.value.origins if is_sensitive_value(self.value) else ()


@final
class SelectionViewProvenance(msgspec.Struct, frozen=True, kw_only=True):
    """Safe decoder provenance for one storage-backed selected view."""

    view_id: ViewId
    source: SelectorSource
    requested: BodyViewKind
    decoder: DecoderSpec
    auto_selection: AutoViewSelection | None
    media: MediaTypeMetadata | None
    completeness: ViewCompleteness

    def __post_init__(self) -> None:
        """Require exact safe identities without retaining a storage locator."""
        if type(self.view_id) is not ViewId:
            raise TypeError("selection view provenance requires a ViewId")
        if type(self.source) is not SelectorSource:
            raise TypeError("selection view provenance requires a SelectorSource")
        if type(self.requested) is not BodyViewKind:
            raise TypeError("selection view provenance requires a requested body view")
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("selection view provenance requires a DecoderSpec")
        if self.auto_selection is not None and type(self.auto_selection) is not AutoViewSelection:
            raise TypeError("selection view auto provenance must be AutoViewSelection")
        if self.auto_selection is not None and self.auto_selection.decoder != self.decoder:
            raise ValueError("selection view auto provenance contradicts its decoder")
        if self.media is not None and not is_media_type_metadata(self.media):
            raise TypeError("selection view media must be typed response media metadata")
        if type(self.completeness) is not ViewCompleteness:
            raise TypeError("selection view completeness must be ViewCompleteness")
        if (self.requested is BodyViewKind.AUTO) != (self.auto_selection is not None):
            raise ValueError("exactly automatic view selection requires auto provenance")


def require_match_contract(
    match: SelectionMatch,
    selector: CompiledSelector,
    environment_execution_id: EnvironmentExecutionId,
) -> None:
    """Bind one concrete match to the compiled selector and execution contract."""
    if location_environment_execution_id(match.location) != environment_execution_id:
        raise ValueError("selection match belongs to a different Environment execution")
    require_location_matches_selector(match.location, selector)
    value_type = match.value.payload_type if is_sensitive_value(match.value) else type(match.value)
    kind = selected_value_kind_type(value_type)
    if kind is None or not contract_accepts_runtime_kind(selector.output, kind):
        raise ValueError("selection match value kind contradicts selector output contract")
    if value_type is BareQueryFieldValue and selector.source is not SelectorSource.REQUEST_QUERY:
        raise ValueError("bare query values require the request-query selector source")
    if is_sensitive_value(match.value) and value_type is BareQueryFieldValue:
        raise ValueError("bare query values cannot carry sensitive payload containment")


def require_match_collection_contract(
    matches: tuple[SelectionMatch, ...],
    selector: CompiledSelector,
) -> None:
    """Validate duplicate and cardinality guarantees on a complete match tuple."""
    locations = tuple(match.location for match in matches)
    if not selector.capabilities.may_duplicate and len(set(locations)) != len(locations):
        raise ValueError("selector without duplicate semantics cannot repeat a location")
    cardinality = selector.capabilities.cardinality
    if cardinality is SelectorCardinality.EXACTLY_ONE and len(matches) != 1:
        raise ValueError("exactly-one selector result must contain exactly one match")
    if cardinality is SelectorCardinality.ZERO_OR_ONE and len(matches) > 1:
        raise ValueError("zero-or-one selector result cannot contain more than one match")


def require_view_provenance(
    views: object,
    selector: CompiledSelector,
    locations: tuple[ConcreteLocation, ...],
) -> None:
    """Bind every view-backed location to unique safe view provenance."""
    if type(views) is not tuple or any(type(view) is not SelectionViewProvenance for view in views):
        raise TypeError("selection views must be an immutable provenance tuple")
    identifiers = tuple(view.view_id for view in views)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("selection view provenance identifiers must be unique")
    if any(view.source is not selector.source for view in views):
        raise ValueError("selection view provenance source contradicts its selector")
    located = tuple(
        view_id for location in locations if (view_id := _location_view_id(location)) is not None
    )
    if not set(located) <= set(identifiers):
        raise ValueError("view-backed selection values require exact view provenance")


def _location_view_id(location: ConcreteLocation) -> ViewId | None:
    if type(location) is JsonLocation:
        return None if location.source is SelectorSource.SCENARIO_STEP else location.view_id
    if type(location) is ViewLocation:
        return location.view_id
    if type(location) is StreamLocation:
        return location.view_id
    return None


def _require_handle_identity(match: SelectionMatch) -> None:
    if is_sensitive_value(match.value):
        if match.value.payload_type in {BodyId, ViewId}:
            raise ValueError("selector handle identities cannot be sensitive")
        return
    if type(match.value) is BodyId and (
        type(match.location) is not BodyLocation or match.location.body_id != match.value
    ):
        raise ValueError("selected BodyId must equal its concrete body location")
    if type(match.value) is not ViewId:
        return
    if type(match.location) not in {ViewLocation, JsonLocation, StreamLocation}:
        raise ValueError("selected ViewId requires a concrete view location")
    location = cast("ViewLocation | JsonLocation | StreamLocation", match.location)
    if location.view_id != match.value:
        raise ValueError("selected ViewId must equal its concrete view location")
