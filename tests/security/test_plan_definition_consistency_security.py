"""Security regression for cross-slice resource-definition substitution."""

import msgspec
import pytest

from tests.core.domain.planning_factories import _definition
from tests.core.domain.planning_factories import _run_plan_for_slices
from tests.core.domain.planning_factories import _slice


def test_same_request_identity_cannot_substitute_another_compiled_fingerprint() -> None:
    trusted = _slice()
    original = trusted.cells.subject
    substituted = msgspec.structs.replace(
        original,
        fingerprint=_definition(
            original.identity.kind,
            original.identity.resource_id.value,
            digest="0",
        ).fingerprint,
    )
    hostile = msgspec.structs.replace(
        trusted,
        cells=msgspec.structs.replace(trusted.cells, subject=substituted),
    )

    with pytest.raises(ValueError, match="identity maps to multiple plan fingerprints"):
        _run_plan_for_slices(trusted, hostile)
