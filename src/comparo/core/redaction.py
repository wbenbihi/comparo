"""Scrub real secret values from anything that leaves the process.

The DISPLAY sink masks secrets in the TUI, but drift and assertion *details* are
built from real EXECUTE-sink responses — and a server can echo a secret straight
back into a body it drifts on. So reports, exports, and the saved ``.reports/``
archive must be scrubbed against the real secret values before they are written.
This module is that single string-match backstop.
"""

import dataclasses
import json
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from comparo.core.loader import LoadedProject
from comparo.core.models import Environment
from comparo.core.secrets import ExecuteSecrets
from comparo.core.secrets import SecretUnavailableError

#: What a redacted secret becomes — the same glyph the DISPLAY sink uses.
MASK = "••••••"

#: Response header names whose value is a credential the *server* issues (never a
#: declared secret, so the value-match redactor would miss it). Masked by name.
_CREDENTIAL_HEADERS = frozenset(
    {
        "set-cookie",
        "cookie",
        "authorization",
        "proxy-authorization",
        "www-authenticate",
        "proxy-authenticate",
        "x-api-key",
    }
)


def mask_credential_header(name: str, value: str) -> str:
    """Mask *value* when *name* is a credential-bearing header, else return it.

    Redaction elsewhere only masks values of *declared* secrets; a server can
    hand back a session cookie or echo an ``Authorization`` header that was never
    declared, so those header values are masked by name as a policy backstop.
    """
    return MASK if name.strip().lower() in _CREDENTIAL_HEADERS else value


#: A recursion cap so a pathologically deep body (a server can send one) is
#: redacted as an opaque leaf instead of overflowing the stack — far beyond any
#: realistic API payload nesting, and matching the diff engine's cap.
_MAX_REDACT_DEPTH = 200


def redact_tree(value: object, redact: Callable[[str], str], _depth: int = 0) -> object:
    """Recursively mask secrets in a parsed value — object keys and strings alike.

    A server can echo a secret as a JSON *key* as well as a value, so both are
    redacted before the tree is serialized to a report, an export, or the archive.
    The single home for request/response body, ``events``, and ``FieldDiff`` /
    ``AssertionResult`` value redaction.
    """
    if isinstance(value, str):
        return redact(value)
    if _depth >= _MAX_REDACT_DEPTH:
        # Too deep to recurse safely; stringify-and-redact the remaining subtree as
        # one leaf so a hostile payload can't overflow the stack (nor slip through
        # unmasked). ``default=str`` keeps a non-JSON value from crashing.
        return redact(json.dumps(value, ensure_ascii=False, default=str))
    if isinstance(value, dict):
        return {
            redact(str(key)): redact_tree(item, redact, _depth + 1) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_tree(item, redact, _depth + 1) for item in value]
    return value


def environment_secret_values(environment: Environment, root: Path) -> set[str]:
    """One environment's resolved secret values (``$file`` sources confined to *root*)."""
    values: set[str] = set()
    sources = environment.spec.secrets or {}
    secrets = ExecuteSecrets(dict(sources), root)
    for name in sources:
        try:
            value = secrets[name]
        except SecretUnavailableError:
            # The source is simply absent (unset $env, exhausted `from` chain): the
            # value was never available this session, so a response cannot have
            # echoed it. Skipping it does not shrink the mask over a live secret.
            continue
        # Any other SecretError — an unreadable or root-escaping $file, i.e. a
        # declared secret we cannot read *now* though it may have been sent — is
        # fatal: fail closed rather than silently drop it from the mask and risk
        # writing that secret to a report or the archive.
        if value:
            values.add(value)
    return values


def secret_values(project: LoadedProject) -> set[str]:
    """Every environment's resolved secret values (a superset masked everywhere)."""
    values: set[str] = set()
    for obj in project.objects.values():
        if isinstance(obj, Environment):
            values |= environment_secret_values(obj, project.root)
    return values


def _encoded_forms(value: str) -> set[str]:
    r"""Every serialized form a secret can take once it reaches a sink.

    A detail or body is ``json.dumps``-ed *before* a sink redacts it, so a secret
    containing ``"``/``\``/newline appears escaped (``p@ss\"w0rd``) and a raw
    substring match would miss it. A secret sent in a URL is percent-encoded, and
    a server can echo the request URL back into an error/Location. Some sinks also
    case-fold what they normalize — a secret reflected as a response header NAME
    reaches the ``$headers`` diff namespace lowercased — so the case-folded form
    (and its encodings) registers too. Registering every form closes each leak.
    """
    forms: set[str] = set()
    for variant in {value, value.lower()}:
        forms.add(variant)
        for ensure_ascii in (False, True):
            forms.add(json.dumps(variant, ensure_ascii=ensure_ascii)[1:-1])
        forms.add(urllib.parse.quote(variant))  # path-encoded (leaves "/" as-is)
        forms.add(urllib.parse.quote(variant, safe=""))  # fully encoded ("/" -> %2F)
        forms.add(urllib.parse.quote_plus(variant))  # form-encoded (" " -> "+")
    return forms


@dataclasses.dataclass(frozen=True, slots=True)
class Redactor:
    """Masks any known secret value found in a string."""

    values: tuple[str, ...]

    @classmethod
    def from_values(cls, values: "set[str]") -> "Redactor":
        """Build a redactor over raw secret *values*, adding their encoded forms.

        Longest-first, so a secret that contains a shorter one is masked whole
        (and an escaped form, always ≥ its raw value, is tried before the raw).
        """
        forms: set[str] = set()
        for value in values:
            forms |= _encoded_forms(value)
        return cls(tuple(sorted(forms, key=len, reverse=True)))

    @classmethod
    def for_project(cls, project: LoadedProject) -> "Redactor":
        """Build a redactor over every resolved secret value in *project*.

        The string-match backstop is a security *floor*: it is ALWAYS active for
        every sink — the TUI display, the saved ``runs`` export, the ``.reports``
        archive, and the CLI report files — regardless of
        ``spec.redaction.stringMatchBackstop``. The config key is accepted for
        forward-compatibility but can never turn masking off, because doing so
        would write a server-echoed secret to disk.
        """
        return cls.from_values(secret_values(project))

    def text(self, text: str) -> str:
        """Return *text* with every known secret value replaced by the mask."""
        for value in self.values:
            if value in text:
                text = text.replace(value, MASK)
        return text


#: A redact callable — a ``Redactor.text`` or ``str`` (identity) when nothing to mask.
Redact = Callable[[str], str]
