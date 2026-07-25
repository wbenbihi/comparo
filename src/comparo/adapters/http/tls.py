"""Explicit TLS context construction without ambient certificate configuration."""

from __future__ import annotations

import ssl
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from typing import final

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.policies import TrustStoreRef


class TlsConfigurationFailureCode(DomainStrEnum):
    """Stable path- and material-free TLS setup failure classes."""

    MISSING_TRUST_STORE = "missing-trust-store"
    MISSING_CLIENT_IDENTITY = "missing-client-identity"
    SYSTEM_TRUST_UNAVAILABLE = "system-trust-unavailable"
    INVALID_MATERIAL = "invalid-material"


@final
class TlsConfigurationError(Exception):
    """A value-free TLS configuration failure safe for runtime diagnostics."""

    __slots__ = ("code",)

    def __init__(self, code: TlsConfigurationFailureCode) -> None:
        """Retain only one stable failure code."""
        if type(code) is not TlsConfigurationFailureCode:
            raise TypeError("TLS configuration failure code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class ClientIdentityFiles:
    """Canonical certificate-chain and private-key files for one logical identity."""

    __slots__ = ("certificate_chain", "private_key")

    def __init__(self, *, certificate_chain: Path, private_key: Path) -> None:
        """Resolve two explicit absolute regular files to canonical paths."""
        self.certificate_chain = _canonical_file(certificate_chain)
        self.private_key = _canonical_file(private_key)


class ClientKeyPasswordProvider(Protocol):
    """Reveal an optional encrypted-key password only at SSL context construction."""

    def password_for(self, identity: ClientIdentityRef) -> str | None:
        """Return a live password or None without retaining it in TLS observations."""
        ...


class NoClientKeyPasswords:
    """Default provider for unencrypted private-key files."""

    def password_for(self, identity: ClientIdentityRef) -> None:
        """Return no password without consulting ambient sources."""
        del identity
        return


class TlsContextProvider(Protocol):
    """Construct independent SSL contexts for exact target or proxy TLS policies."""

    def create(self, policy: TlsPolicy) -> ssl.SSLContext:
        """Return a new configured context for one Environment-local connection pool."""
        ...


class ExplicitTlsContextProvider:
    """Resolve logical TLS references through an explicit immutable file registry."""

    __slots__ = ("_client_identities", "_passwords", "_trust_stores")

    def __init__(
        self,
        *,
        trust_stores: Mapping[str, Path] | None = None,
        client_identities: Mapping[str, ClientIdentityFiles] | None = None,
        passwords: ClientKeyPasswordProvider | None = None,
    ) -> None:
        """Validate and snapshot logical registries without reading ambient TLS variables."""
        if trust_stores is not None and not isinstance(trust_stores, Mapping):
            raise TypeError("TLS trust-store registry must be a Mapping")
        if client_identities is not None and not isinstance(client_identities, Mapping):
            raise TypeError("TLS client-identity registry must be a Mapping")
        if any(type(key) is not str for key in (trust_stores or {})):
            raise TypeError("TLS trust-store registry keys must be strings")
        try:
            self._trust_stores = {
                key: _canonical_file(path) for key, path in (trust_stores or {}).items()
            }
        except (TypeError, ValueError, OSError) as error:
            raise TlsConfigurationError(TlsConfigurationFailureCode.INVALID_MATERIAL) from error
        identities = dict(client_identities or {})
        if any(
            type(key) is not str or type(value) is not ClientIdentityFiles
            for key, value in identities.items()
        ):
            raise TypeError("TLS client-identity registry contains an unsupported entry")
        self._client_identities = identities
        self._passwords = passwords or NoClientKeyPasswords()
        if not hasattr(self._passwords, "password_for"):
            raise TypeError("TLS key-password provider must implement password_for")

    def create(self, policy: TlsPolicy) -> ssl.SSLContext:
        """Build one context using only the exact supplied policy and explicit registry."""
        if type(policy) is not TlsPolicy:
            raise TypeError("TLS context provider requires TlsPolicy")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            _configure_verification(context, policy, self._trust_stores)
            context.minimum_version = {
                TlsVersion.TLS_1_2: ssl.TLSVersion.TLSv1_2,
                TlsVersion.TLS_1_3: ssl.TLSVersion.TLSv1_3,
            }[policy.minimum_version]
            if policy.client_identity is not None:
                self._load_client_identity(context, policy.client_identity)
        except TlsConfigurationError:
            raise
        except (OSError, ssl.SSLError, ValueError) as error:
            raise TlsConfigurationError(TlsConfigurationFailureCode.INVALID_MATERIAL) from error
        return context

    def _load_client_identity(
        self,
        context: ssl.SSLContext,
        reference: ClientIdentityRef,
    ) -> None:
        identity = self._client_identities.get(reference.value)
        if identity is None:
            raise TlsConfigurationError(TlsConfigurationFailureCode.MISSING_CLIENT_IDENTITY)
        password = self._passwords.password_for(reference)
        try:
            context.load_cert_chain(
                certfile=identity.certificate_chain,
                keyfile=identity.private_key,
                password=password,
            )
        finally:
            password = None


def _canonical_file(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise TypeError("TLS material path must be an absolute pathlib.Path")
    exact = path.resolve(strict=True)
    if not exact.is_file():
        raise ValueError("TLS material must identify a regular file")
    return exact


def _configure_verification(
    context: ssl.SSLContext,
    policy: TlsPolicy,
    trust_stores: Mapping[str, Path],
) -> None:
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED if policy.verify_certificates else ssl.CERT_NONE
    context.check_hostname = policy.verify_hostname
    if not policy.verify_certificates:
        return
    if policy.trust_source is TrustSource.CUSTOM:
        reference = policy.custom_trust_store
        if type(reference) is not TrustStoreRef:  # pragma: no cover - policy invariant
            raise TypeError("custom trust policy requires TrustStoreRef")
        path = trust_stores.get(reference.value)
        if path is None:
            raise TlsConfigurationError(TlsConfigurationFailureCode.MISSING_TRUST_STORE)
        context.load_verify_locations(cafile=path)
        return
    defaults = ssl.get_default_verify_paths()
    cafile = Path(defaults.openssl_cafile) if defaults.openssl_cafile else None
    capath = Path(defaults.openssl_capath) if defaults.openssl_capath else None
    cafile_value = cafile if cafile is not None and cafile.is_file() else None
    capath_value = capath if capath is not None and capath.is_dir() else None
    if cafile_value is None and capath_value is None:
        raise TlsConfigurationError(TlsConfigurationFailureCode.SYSTEM_TRUST_UNAVAILABLE)
    context.load_verify_locations(cafile=cafile_value, capath=capath_value)
