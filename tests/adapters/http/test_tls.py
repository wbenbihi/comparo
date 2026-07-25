"""Explicit TLS material and ambient-isolation tests."""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from comparo.adapters.http.tls import ClientIdentityFiles
from comparo.adapters.http.tls import ClientKeyPasswordProvider
from comparo.adapters.http.tls import ExplicitTlsContextProvider
from comparo.adapters.http.tls import NoClientKeyPasswords
from comparo.adapters.http.tls import TlsConfigurationError
from comparo.adapters.http.tls import TlsConfigurationFailureCode
from comparo.adapters.http.tls import _configure_verification
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.policies import TrustStoreRef


def _policy(
    *,
    verify: bool = False,
    hostname: bool = False,
    trust: TrustSource = TrustSource.OPERATING_SYSTEM,
    trust_ref: TrustStoreRef | None = None,
    identity: ClientIdentityRef | None = None,
    minimum: TlsVersion = TlsVersion.TLS_1_2,
) -> TlsPolicy:
    return TlsPolicy(
        verify_certificates=verify,
        verify_hostname=hostname,
        minimum_version=minimum,
        trust_source=trust,
        custom_trust_store=trust_ref,
        client_identity=identity,
    )


def test_unverified_context_is_explicit_and_never_loads_ambient_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/untrusted/ambient.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/untrusted/ambient-directory")
    context = ExplicitTlsContextProvider().create(_policy(minimum=TlsVersion.TLS_1_3))
    assert context.verify_mode == ssl.CERT_NONE
    assert not context.check_hostname
    assert context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert os.environ["SSL_CERT_FILE"] == "/untrusted/ambient.pem"


def test_custom_trust_and_identity_references_fail_closed_without_paths() -> None:
    provider = ExplicitTlsContextProvider()
    with pytest.raises(TlsConfigurationError) as trust:
        provider.create(
            _policy(
                verify=True,
                trust=TrustSource.CUSTOM,
                trust_ref=TrustStoreRef(value="project-ca"),
            )
        )
    assert trust.value.code is TlsConfigurationFailureCode.MISSING_TRUST_STORE
    assert "project-ca" not in str(trust.value)

    with pytest.raises(TlsConfigurationError) as identity:
        provider.create(_policy(identity=ClientIdentityRef(value="service-client")))
    assert identity.value.code is TlsConfigurationFailureCode.MISSING_CLIENT_IDENTITY
    assert "service-client" not in str(identity.value)


def test_registry_validates_material_and_contains_ssl_errors(
    tmp_path: Path,
) -> None:
    class RecordingContext:
        check_hostname = False
        verify_mode = ssl.CERT_NONE

        def __init__(self) -> None:
            self.loaded: Path | None = None

        def load_verify_locations(
            self,
            cafile: Path | None = None,
            capath: Path | None = None,
        ) -> None:
            del capath
            self.loaded = cafile

    relative = Path("relative.pem")
    with pytest.raises(TlsConfigurationError) as invalid_registry:
        ExplicitTlsContextProvider(trust_stores={"ca": relative})
    assert invalid_registry.value.code is TlsConfigurationFailureCode.INVALID_MATERIAL

    invalid = tmp_path / "invalid.pem"
    invalid.write_text("not a certificate", encoding="utf-8")
    recording = RecordingContext()
    custom = _policy(
        verify=True,
        trust=TrustSource.CUSTOM,
        trust_ref=TrustStoreRef(value="ca"),
    )
    _configure_verification(cast("ssl.SSLContext", recording), custom, {"ca": invalid})
    assert recording.loaded == invalid
    provider = ExplicitTlsContextProvider(trust_stores={"ca": invalid})
    with pytest.raises(TlsConfigurationError) as invalid_material:
        provider.create(custom)
    assert invalid_material.value.code is TlsConfigurationFailureCode.INVALID_MATERIAL
    assert str(invalid) not in str(invalid_material.value)


def test_client_identity_files_and_provider_boundaries(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    identity = ClientIdentityFiles(certificate_chain=cert, private_key=key)
    assert identity.certificate_chain == cert.resolve()
    assert identity.private_key == key.resolve()

    with pytest.raises(TypeError, match="Mapping"):
        ExplicitTlsContextProvider(trust_stores=cast("dict[str, Path]", object()))
    with pytest.raises(TypeError, match="Mapping"):
        ExplicitTlsContextProvider(
            client_identities=cast("dict[str, ClientIdentityFiles]", object())
        )
    with pytest.raises(TypeError, match="unsupported entry"):
        ExplicitTlsContextProvider(
            client_identities={"identity": cast("ClientIdentityFiles", object())}
        )
    with pytest.raises(TypeError, match="keys must be strings"):
        ExplicitTlsContextProvider(trust_stores={cast("str", 1): cert})
    with pytest.raises(TypeError, match="password_for"):
        ExplicitTlsContextProvider(passwords=cast("ClientKeyPasswordProvider", object()))
    with pytest.raises(TypeError, match="TlsPolicy"):
        ExplicitTlsContextProvider().create(cast("TlsPolicy", object()))
    with pytest.raises(TypeError, match="failure code"):
        TlsConfigurationError(cast("TlsConfigurationFailureCode", object()))

    directory = tmp_path / "material-directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        ClientIdentityFiles(certificate_chain=directory, private_key=key)


def test_system_trust_uses_compiled_paths_and_fails_when_they_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ExplicitTlsContextProvider().create(_policy(verify=True, hostname=True))
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2

    missing = SimpleNamespace(openssl_cafile=None, openssl_capath=None)
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: missing)
    with pytest.raises(TlsConfigurationError) as failure:
        ExplicitTlsContextProvider().create(_policy(verify=True))
    assert failure.value.code is TlsConfigurationFailureCode.SYSTEM_TRUST_UNAVAILABLE


def test_registered_client_identity_contains_key_loading_failures(tmp_path: Path) -> None:
    cert = tmp_path / "client-cert.pem"
    key = tmp_path / "client-key.pem"
    cert.write_text("invalid certificate", encoding="utf-8")
    key.write_text("invalid key", encoding="utf-8")
    identity = ClientIdentityFiles(certificate_chain=cert, private_key=key)
    provider = ExplicitTlsContextProvider(client_identities={"client": identity})
    NoClientKeyPasswords().password_for(ClientIdentityRef(value="client"))
    with pytest.raises(TlsConfigurationError) as failure:
        provider.create(_policy(identity=ClientIdentityRef(value="client")))
    assert failure.value.code is TlsConfigurationFailureCode.INVALID_MATERIAL
    assert str(cert) not in str(failure.value)
