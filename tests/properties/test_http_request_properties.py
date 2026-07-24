"""Generative checks for HTTP request canonicalization and route pinning."""

from __future__ import annotations

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination


@given(st.sampled_from((*range(9), *range(10, 32), 127)))
def test_header_values_reject_every_ascii_control_character(codepoint: int) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        HeaderText(value=f"before{chr(codepoint)}after")


@given(st.sampled_from(tuple(string.ascii_letters + string.digits + "-._~")))
def test_encoded_unreserved_characters_have_one_canonical_literal_form(character: str) -> None:
    escape = f"%{ord(character):02X}"
    with pytest.raises(ValueError, match="must not be percent encoded"):
        EncodedPathSegment(value=escape)
    if character == ".":
        with pytest.raises(ValueError, match="dot segments"):
            EncodedPathSegment(value=character)
    else:
        assert EncodedPathSegment(value=character).value == character


@given(st.integers(min_value=0, max_value=31) | st.just(127))
def test_encoded_path_segments_reject_percent_encoded_controls(codepoint: int) -> None:
    with pytest.raises(ValueError, match="encoded control"):
        EncodedPathSegment(value=f"%{codepoint:02X}")


@given(
    st.lists(
        st.integers(min_value=0, max_value=999).map(str),
        min_size=1,
        max_size=4,
    )
)
def test_numeric_only_host_spellings_never_enter_the_dns_name_type(labels: list[str]) -> None:
    with pytest.raises(ValueError, match="numeric-only"):
        DnsHost(value=".".join(labels))


@given(
    st.lists(
        st.integers(min_value=1, max_value=254),
        min_size=1,
        max_size=8,
        unique=True,
    ),
    st.integers(min_value=0),
)
def test_selected_connect_address_must_be_one_of_complete_dns_results(
    final_octets: list[int],
    selected_index: int,
) -> None:
    addresses = tuple(Ipv4Host(value=f"192.0.2.{octet}") for octet in final_octets)
    selected = addresses[selected_index % len(addresses)]
    destination = NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="api.example.test"),
        port=HttpPort(value=443),
        resolved_addresses=addresses,
        selected_address=selected,
    )
    assert destination.selected_address in destination.resolved_addresses

    outside_octet = next(octet for octet in range(1, 255) if octet not in final_octets)
    with pytest.raises(ValueError, match="one of the resolved"):
        NetworkDestination(
            scheme=HttpScheme.HTTPS,
            host=DnsHost(value="api.example.test"),
            port=HttpPort(value=443),
            resolved_addresses=addresses,
            selected_address=Ipv4Host(value=f"192.0.2.{outside_octet}"),
        )
