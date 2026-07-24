"""Smoke tests for the refactored package scaffold."""


def test_domain_package_is_importable() -> None:
    """The isolated domain package is importable."""
    from comparo.core import domain

    assert domain.__name__ == "comparo.core.domain"
