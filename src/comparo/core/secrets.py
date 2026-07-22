"""Resolve environment secrets from their sources for the execute sink.

Sources are resolved lazily and cached, so a secret that is declared but
unavailable only fails a run if something actually uses it.
"""

import dataclasses
import os
from pathlib import Path


class SecretError(Exception):
    """Raised when a required secret cannot be resolved from its source."""


class SecretUnavailableError(SecretError):
    """A secret whose source is simply *absent*.

    An unset ``$env`` var, an undeclared name, or a fully-exhausted ``$from`` chain.
    Distinguished from a plain :class:`SecretError` (an *anomalous* failure — an
    unreadable or root-escaping ``$file``, an unsupported source shape) so the
    redactor can skip a benign gap while still failing closed on a declared
    secret it cannot read. The value was never available this session, so it
    cannot have been echoed back into a response.
    """


@dataclasses.dataclass(slots=True)
class ExecuteSecrets:
    """Resolves declared secrets to real values on demand."""

    sources: dict[str, object]
    root: Path
    _cache: dict[str, str] = dataclasses.field(default_factory=dict)

    def __getitem__(self, name: str) -> str:
        """Resolve *name* to its secret value, caching the result.

        Args:
            name: The secret name to resolve.

        Returns:
            The resolved secret value.

        Raises:
            SecretError: If the secret is undeclared or its source is unavailable.
        """
        if name in self._cache:
            return self._cache[name]
        if name not in self.sources:
            message = f"no secret named '{name}'"
            raise SecretUnavailableError(message)
        value = _resolve(name, self.sources[name], self.root)
        self._cache[name] = value
        return value


def _resolve(name: str, source: object, root: Path) -> str:
    if isinstance(source, dict):
        if "$env" in source:
            variable = str(source["$env"])
            value = os.environ.get(variable)
            if value is None:
                message = f"secret '{name}': environment variable '{variable}' is not set"
                raise SecretUnavailableError(message)
            return value
        if "$literal" in source:
            return str(source["$literal"])
        if "$file" in source:
            base = root.resolve()
            path = (base / str(source["$file"])).resolve()
            if not path.is_relative_to(base):
                message = f"secret '{name}': $file path escapes the project root: {source['$file']}"
                raise SecretError(message)
            try:
                return path.read_text(encoding="utf-8").strip()
            except (OSError, ValueError, LookupError) as error:
                message = f"secret '{name}': cannot read {path}"
                raise SecretError(message) from error
        candidates = source.get("$from")
        if isinstance(candidates, list):
            for candidate in candidates:
                try:
                    return _resolve(name, candidate, root)
                except SecretUnavailableError:
                    # A benign absence (unset $env, exhausted sub-chain) — try the
                    # next source. An anomalous SecretError (unreadable or
                    # root-escaping $file) is fatal and propagates: a fallback must
                    # never silently mask a real misconfiguration.
                    continue
            # An explicit fallback chain that resolves to nothing: the secret is
            # unavailable this session (it would also fail at execute time, so it
            # was never sent). Benign for the redactor to skip.
            message = f"secret '{name}': no source in '$from' resolved"
            raise SecretUnavailableError(message)
    message = f"secret '{name}': unsupported source"
    raise SecretError(message)
