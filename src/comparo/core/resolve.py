"""Resolve a request against an environment into a concrete, masked tree.

This is the by-reference sink: the loader keeps ``$ref``/``$val``/``${...}`` as
holes, and the resolver fills them for a chosen environment. The display sink
(the default) masks secrets and records a provenance trail; the execute sink
(injecting real secret values) arrives with the HTTP engine in a later milestone.
"""

import dataclasses
import enum

from comparo.core.interpolation import Context
from comparo.core.interpolation import interpolate
from comparo.core.loader import LoadedProject
from comparo.core.models import Environment
from comparo.core.models import Instance
from comparo.core.models import Request
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail

_SECRET_SIGILS = ("$secret", "$env", "$file")


class EnvironmentSelectionError(Exception):
    """Raised when the requested environment cannot be found."""


def select_environment(project: LoadedProject, requested: str | None) -> Environment:
    """Pick an environment by name or id, falling back to the project default.

    An environment may be named by its full ``metadata.id`` (``environment.prod``)
    or by the short segment after the prefix (``prod``).

    Args:
        project: The loaded project to search.
        requested: An environment name or id, or ``None`` for the project default.

    Returns:
        The matching environment.

    Raises:
        EnvironmentSelectionError: If no environment matches or none is specified.
    """
    environments = {i: o for i, o in project.objects.items() if isinstance(o, Environment)}
    name = requested
    if name is None and project.project is not None:
        config = project.project.spec.environments
        if isinstance(config, dict):
            default = config.get("default")
            name = default if isinstance(default, str) else None
    if name is None:
        message = "no environment given and the project declares no default"
        raise EnvironmentSelectionError(message)
    for identifier, environment in environments.items():
        if identifier == name or identifier.split(".", 1)[-1] == name:
            return environment
    known = ", ".join(sorted(environments)) or "none"
    message = f"unknown environment '{name}' (known: {known})"
    raise EnvironmentSelectionError(message)


class Sink(enum.Enum):
    """Which resolution sink to produce."""

    DISPLAY = "display"
    EXECUTE = "execute"


@dataclasses.dataclass(slots=True)
class ResolvedRequest:
    """A request resolved for one environment. Secret values are masked."""

    method: str
    url: str
    headers: list[tuple[str, object]]
    query: dict[str, object]
    body: object
    trail: list[Trail]


class Resolver:
    """Resolves a :class:`Request` against an :class:`Environment`."""

    def __init__(
        self, project: LoadedProject, environment: Environment, sink: Sink = Sink.DISPLAY
    ) -> None:
        """Build a resolver bound to a project and one environment.

        Args:
            project: The loaded project, used to resolve ``$val`` instances.
            environment: The environment whose variables and secrets apply.
            sink: Which sink to produce; only ``DISPLAY`` is supported so far.
        """
        self.project = project
        self.environment = environment
        self.sink = sink
        self.context = Context(
            variables=dict(environment.spec.variables or {}),
            secret_names=frozenset((environment.spec.secrets or {}).keys()),
        )

    def resolve_request(self, request: Request) -> ResolvedRequest:
        """Resolve *request* into a concrete tree with a provenance trail.

        Args:
            request: The request object to resolve.

        Returns:
            The resolved request; secret-tainted values are masked.
        """
        outbound = request.spec.request
        base = self.environment.spec.base_url.rstrip("/")
        url = f"{base}/{outbound.endpoint.lstrip('/')}"
        trail: list[Trail] = []
        headers = self._headers(outbound.headers, trail)
        query = {
            key: self._value(value, f"query.{key}", trail)
            for key, value in (outbound.query or {}).items()
        }
        body = None if outbound.body is None else self._value(outbound.body, "body", trail)
        return ResolvedRequest(outbound.method, url, headers, query, body, trail)

    def _headers(self, request_headers: object, trail: list[Trail]) -> list[tuple[str, object]]:
        merged: dict[str, tuple[str, object]] = {}
        for header in self.environment.spec.headers or []:
            merged[header.key.lower()] = (header.key, header.value)
        for key, raw in self._header_pairs(request_headers, trail):
            merged[key.lower()] = (key, raw)
        return [
            (key, self._value(raw, f"headers.{key.lower()}", trail)) for key, raw in merged.values()
        ]

    def _header_pairs(
        self, request_headers: object, trail: list[Trail]
    ) -> list[tuple[str, object]]:
        node = request_headers
        if isinstance(node, dict):
            hole = _hole(node)
            if hole is not None and hole[0] == "$val" and isinstance(hole[1], str):
                trail.append(Trail("headers", Origin.INSTANCE, hole[1]))
                node = self._instance_value(hole[1])
        pairs: list[tuple[str, object]] = []
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and "key" in item:
                    pairs.append((str(item["key"]), item.get("value")))
        return pairs

    def _value(self, node: object, path: str, trail: list[Trail]) -> object:
        if isinstance(node, str):
            result = interpolate(node, self.context)
            if result.origin is not Origin.LITERAL and result.detail is not None:
                trail.append(Trail(path, result.origin, result.detail))
            return result.value
        if isinstance(node, dict):
            hole = _hole(node)
            if hole is not None:
                return self._reference(hole[0], hole[1], path, trail)
            return {key: self._value(value, _join(path, key), trail) for key, value in node.items()}
        if isinstance(node, list):
            return [self._value(item, f"{path}[{index}]", trail) for index, item in enumerate(node)]
        return node

    def _reference(self, sigil: str, target: object, path: str, trail: list[Trail]) -> object:
        if sigil == "$val" and isinstance(target, str):
            trail.append(Trail(path, Origin.INSTANCE, target))
            return self._value(self._instance_value(target), path, trail)
        if sigil == "$literal":
            return target
        if sigil in _SECRET_SIGILS:
            trail.append(Trail(path, Origin.SECRET, f"{sigil}:{target}"))
            return self.context.mask
        return {sigil: target}

    def _instance_value(self, identifier: str) -> object:
        instance = self.project.objects.get(identifier)
        return instance.spec.value if isinstance(instance, Instance) else None


def _hole(node: dict[object, object]) -> tuple[str, object] | None:
    if len(node) != 1:
        return None
    (key, value) = next(iter(node.items()))
    if isinstance(key, str) and key.startswith("$"):
        return key, value
    return None


def _join(path: str, key: object) -> str:
    return f"{path}.{key}" if path else str(key)
