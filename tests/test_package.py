"""Smoke tests for the refactored package scaffold."""


def test_domain_package_is_importable() -> None:
    """The isolated domain package is importable."""
    from comparo.core import domain

    assert domain.__name__ == "comparo.core.domain"


def test_configuration_packages_expose_compiler_and_adapter_boundaries() -> None:
    """Public package surfaces separate pure compilation from filesystem loading."""
    from comparo import configuration
    from comparo.core import configuration as core_configuration

    assert callable(configuration.compile_project_path)
    assert callable(core_configuration.compile_v1alpha1)
    assert callable(core_configuration.generate_v1alpha1_schema)
