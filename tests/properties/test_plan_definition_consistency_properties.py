"""Generative checks for the run plan's global definition invariant."""

import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from tests.core.domain.planning_factories import _run_plan_for_slices
from tests.core.domain.planning_factories import _slice


@given(
    digest=st.text(
        alphabet="0123456789abcdef",
        min_size=64,
        max_size=64,
    ).filter(lambda value: value != "b" * 64)
)
def test_any_different_fingerprint_for_one_plan_identity_is_rejected(digest: str) -> None:
    trusted = _slice()
    request = trusted.cells.subject
    substituted = msgspec.structs.replace(
        request,
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value=digest),
        ),
    )
    hostile = msgspec.structs.replace(
        trusted,
        cells=msgspec.structs.replace(trusted.cells, subject=substituted),
    )
    with pytest.raises(ValueError, match="identity maps to multiple plan fingerprints"):
        _run_plan_for_slices(trusted, hostile)
