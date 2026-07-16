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
from comparo.core.matrix import Injection
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import case_key
from comparo.core.models import Environment
from comparo.core.models import Instance
from comparo.core.models import Request
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail
from comparo.core.secrets import ExecuteSecrets


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


def resolve_pair(
    project: LoadedProject, pair: str | None, baseline: str | None, candidate: str | None
) -> tuple[Environment, Environment]:
    """Resolve a baseline/candidate environment pair.

    Explicit ``baseline`` and ``candidate`` win; otherwise a named (or the only)
    ``diffPair`` from the project manifest is used.

    Args:
        project: The loaded project.
        pair: A diff-pair name, or ``None`` for the first declared pair.
        baseline: An explicit baseline environment name or id.
        candidate: An explicit candidate environment name or id.

    Returns:
        The resolved (baseline, candidate) environments.

    Raises:
        EnvironmentSelectionError: If neither an explicit pair nor a manifest pair applies.
    """
    if baseline is not None and candidate is not None:
        return select_environment(project, baseline), select_environment(project, candidate)
    found = _find_pair(project, pair)
    if found is not None:
        return select_environment(project, found[0]), select_environment(project, found[1])
    message = "specify --pair, or both --baseline and --candidate"
    raise EnvironmentSelectionError(message)


def _find_pair(project: LoadedProject, pair: str | None) -> tuple[str | None, str | None] | None:
    if project.project is None:
        return None
    config = project.project.spec.environments
    pairs = config.get("diffPairs") if isinstance(config, dict) else None
    if not isinstance(pairs, list):
        return None
    for entry in pairs:
        if isinstance(entry, dict) and (pair is None or entry.get("name") == pair):
            found_baseline = entry.get("baseline")
            found_candidate = entry.get("candidate")
            return (
                found_baseline if isinstance(found_baseline, str) else None,
                found_candidate if isinstance(found_candidate, str) else None,
            )
    return None


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
    body_type: str = "json"
    auth: object = None


class Resolver:
    """Resolves a :class:`Request` against an :class:`Environment`."""

    def __init__(
        self, project: LoadedProject, environment: Environment, sink: Sink = Sink.DISPLAY
    ) -> None:
        """Build a resolver bound to a project and one environment.

        Args:
            project: The loaded project, used to resolve ``$val`` instances.
            environment: The environment whose variables and secrets apply.
            sink: Which sink to produce — ``DISPLAY`` masks secrets and records a
                provenance trail; ``EXECUTE`` injects real secret values lazily.
        """
        self.project = project
        self.environment = environment
        self.sink = sink
        secret_sources = environment.spec.secrets or {}
        secret_names = frozenset(secret_sources)
        if sink is Sink.EXECUTE:
            self.context = Context(
                variables=dict(environment.spec.variables or {}),
                secret_names=secret_names,
                mask_secrets=False,
                secret_values=ExecuteSecrets(dict(secret_sources), project.root),
            )
        else:
            self.context = Context(
                variables=dict(environment.spec.variables or {}),
                secret_names=secret_names,
            )

    def resolve_tree(self, value: object) -> tuple[object, list[Trail]]:
        """Resolve an arbitrary value tree, returning it with a provenance trail.

        Used to resolve a standalone value such as an ``Instance`` — every
        ``${...}`` hole and ``$val``/``$secret`` reference is filled the same way
        it would be inside a request.

        Args:
            value: The value tree to resolve.

        Returns:
            The resolved value and the provenance trail of everything filled.
        """
        trail: list[Trail] = []
        resolved = self._value(value, "", trail)
        return resolved, trail

    def resolve_request(self, request: Request, cell: MatrixCell | None = None) -> ResolvedRequest:
        """Resolve *request* into a concrete tree with a provenance trail.

        Args:
            request: The request object to resolve.
            cell: The matrix cell to inject, or ``None`` for the base request.

        Returns:
            The resolved request; secret-tainted values are masked.
        """
        outbound = request.spec.request
        base = self.environment.spec.base_url.rstrip("/")
        endpoint = _inject_path(outbound.endpoint, cell)
        url = f"{base}/{endpoint.lstrip('/')}"
        trail: list[Trail] = []
        headers = self._headers(outbound.headers, trail)
        query = {
            key: self._value(value, f"query.{key}", trail)
            for key, value in (outbound.query or {}).items()
        }
        body = None if outbound.body is None else self._value(outbound.body, "body", trail)
        auth_spec = outbound.auth if outbound.auth is not None else self.environment.spec.auth
        auth = None if auth_spec is None else self._value(auth_spec, "auth", trail)
        resolved = ResolvedRequest(
            outbound.method,
            url,
            headers,
            query,
            body,
            trail,
            body_type=outbound.body_type or "json",
            auth=auth,
        )
        if cell is not None:
            for injection in cell.injections:
                self._inject(resolved, injection, trail)
        return resolved

    def _inject(self, resolved: ResolvedRequest, injection: Injection, trail: list[Trail]) -> None:
        parts = injection.target.split(".")
        if parts and parts[0] == "request":
            parts = parts[1:]
        if not parts:
            return
        trail.append(Trail(".".join(parts), Origin.MATRIX, case_key(injection.case)))
        top, rest = parts[0], parts[1:]
        if top == "query":
            resolved.query = _as_dict(_apply(resolved.query, rest, injection))
        elif top == "body":
            resolved.body = _apply(resolved.body, rest, injection)

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
        if sigil == "$secret" and isinstance(target, str):
            trail.append(Trail(path, Origin.SECRET, f"$secret:{target}"))
            if self.context.mask_secrets:
                return self.context.mask
            return self.context.secret_values[target]
        if sigil in ("$env", "$file"):
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


def _apply(container: object, path: list[str], injection: Injection) -> object:
    if not path:
        if injection.mode == "replace":
            return dict(injection.case)
        merged = dict(container) if isinstance(container, dict) else {}
        merged.update(injection.case)
        return merged
    current = container if isinstance(container, dict) else ({} if injection.create_path else None)
    if current is None:
        return container
    key = path[0]
    child = current.get(key)
    if child is None and injection.create_path:
        child = {}
    updated = dict(current)
    updated[key] = _apply(child, path[1:], injection)
    return updated


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _inject_path(endpoint: str, cell: MatrixCell | None) -> str:
    """Substitute a path matrix's case into ``${key}`` holes in the endpoint.

    A matrix whose ``target`` is ``request.path`` fills placeholders in the
    endpoint template — so ``/status/${code}`` matrixed over codes becomes
    ``/status/200``, ``/status/404``, and so on.

    Args:
        endpoint: The endpoint template.
        cell: The matrix cell to inject, or ``None``.

    Returns:
        The endpoint with any path-matrix placeholders filled.
    """
    if cell is None:
        return endpoint
    for injection in cell.injections:
        if injection.target.split(".")[-1] == "path":
            for key, value in injection.case.items():
                endpoint = endpoint.replace(f"${{{key}}}", str(value))
    return endpoint
