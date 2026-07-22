"""Resolve a request against an environment into a concrete, masked tree.

This is the by-reference sink: the loader keeps ``$use``/``$val``/``${...}`` as
holes, and the resolver fills them for a chosen environment. The display sink
(the default) masks secrets and records a provenance trail; the execute sink
(injecting real secret values) arrives with the HTTP engine in a later milestone.
"""

import dataclasses
import enum
from collections.abc import Mapping

from comparo.core.envfile import load_env_overlay
from comparo.core.loader import LoadedProject
from comparo.core.matrix import Injection
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import case_key
from comparo.core.models import Environment
from comparo.core.models import Header
from comparo.core.models import Instance
from comparo.core.models import Request
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail
from comparo.core.resolution import Context
from comparo.core.resolution import Engine
from comparo.core.resolution import ExecuteSecrets
from comparo.core.resolution import hole


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
        name = config.default if config is not None else None
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
    if baseline is not None or candidate is not None:
        # A lone flag must never be silently discarded in favour of a manifest pair.
        message = "provide both --baseline and --candidate, or neither"
        raise EnvironmentSelectionError(message)
    found = _find_pair(project, pair)
    if found is not None:
        found_baseline, found_candidate = found
        if found_baseline is None or found_candidate is None:
            name = f"'{pair}'" if pair is not None else "the first diff pair"
            message = f"diff pair {name} is missing a baseline or candidate"
            raise EnvironmentSelectionError(message)
        return (
            select_environment(project, found_baseline),
            select_environment(project, found_candidate),
        )
    message = "specify --pair, or both --baseline and --candidate"
    raise EnvironmentSelectionError(message)


def _find_pair(project: LoadedProject, pair: str | None) -> tuple[str | None, str | None] | None:
    if project.project is None:
        return None
    config = project.project.spec.environments
    pairs = config.diff_pairs if config is not None else None
    if not pairs:
        return None
    for entry in pairs:
        if pair is None or entry.name == pair:
            return entry.baseline, entry.candidate
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
    cookies: dict[str, object] | None = None
    streaming: bool = False


class Resolver:
    """Resolves a :class:`Request` against an :class:`Environment`."""

    def __init__(
        self,
        project: LoadedProject,
        environment: Environment,
        sink: Sink = Sink.DISPLAY,
        *,
        cli_env: Mapping[str, str] | None = None,
    ) -> None:
        """Build a resolver bound to a project and one environment.

        Args:
            project: The loaded project, used to resolve ``$val`` instances.
            environment: The environment whose variables and secrets apply.
            sink: Which sink to produce — ``DISPLAY`` masks secrets and records a
                provenance trail; ``EXECUTE`` injects real secret values lazily.
            cli_env: A ``--env-file`` override, merged over the environment's own
                ``envFile`` (CLI wins per key) to form the ``$env`` overlay.
        """
        self.project = project
        self.environment = environment
        self.sink = sink
        # The display sink degrades an unreadable envFile (a preview must not crash);
        # the execute sink fails closed so a real send never proceeds on a half-read file.
        overlay = load_env_overlay(
            environment, project.root, cli_env=cli_env, best_effort=(sink is Sink.DISPLAY)
        )
        secret_sources = environment.spec.secrets or {}
        secret_names = frozenset(secret_sources)
        if sink is Sink.EXECUTE:
            self.context = Context(
                variables=dict(environment.spec.variables or {}),
                secret_names=secret_names,
                mask_secrets=False,
                secret_values=ExecuteSecrets(dict(secret_sources), project.root, env=overlay),
                instances=self._instance_value,
                root=project.root,
                env=overlay,
            )
        else:
            self.context = Context(
                variables=dict(environment.spec.variables or {}),
                secret_names=secret_names,
                instances=self._instance_value,
                root=project.root,
                env=overlay,
            )

    def resolve_tree(self, value: object) -> tuple[object, list[Trail]]:
        """Resolve an arbitrary value tree, returning it with a provenance trail.

        Used to resolve a standalone value such as an ``Instance`` — every
        ``${...}`` hole and ``$val``/``$secret`` reference is filled the same way
        it would be inside a request, by the shared resolution engine.

        Args:
            value: The value tree to resolve.

        Returns:
            The resolved value and the provenance trail of everything filled.
        """
        engine = Engine(self.context)
        return engine.value(value), engine.trail

    def resolve_request(self, request: Request, cell: MatrixCell | None = None) -> ResolvedRequest:
        """Resolve *request* into a concrete tree with a provenance trail.

        Args:
            request: The request object to resolve.
            cell: The matrix cell to inject, or ``None`` for the base request.

        Returns:
            The resolved request; declared secrets are masked in the display sink.
        """
        outbound = request.spec.request
        base = self.environment.spec.base_url.rstrip("/")
        engine = Engine(self.context)
        # Fill path-matrix ``${key}`` holes first, then resolve every hole/string
        # through the shared engine (which records provenance onto ``engine.trail``).
        injected = _inject_path(outbound.endpoint, cell)
        resolved_endpoint = engine.value(injected, "endpoint")
        endpoint = str(resolved_endpoint) if resolved_endpoint is not None else ""
        url = f"{base}/{endpoint.lstrip('/')}"
        headers = self._headers(outbound.headers, engine)
        query = {
            key: engine.value(value, f"query.{key}")
            for key, value in (outbound.query or {}).items()
        }
        body = None if outbound.body is None else engine.value(outbound.body, "body")
        auth_spec = outbound.auth if outbound.auth is not None else self.environment.spec.auth
        auth = None if auth_spec is None else engine.value(auth_spec, "auth")
        cookies = {
            key: engine.value(value, f"cookies.{key}")
            for key, value in (outbound.cookies or {}).items()
        }
        response = request.spec.response
        resolved = ResolvedRequest(
            outbound.method,
            url,
            headers,
            query,
            body,
            engine.trail,
            body_type=outbound.body_type or "json",
            auth=auth,
            cookies=cookies or None,
            streaming=bool(response.streaming) if response is not None else False,
        )
        if cell is not None:
            for injection in cell.injections:
                self._inject(resolved, injection, engine.trail)
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

    def _headers(self, request_headers: object, engine: Engine) -> list[tuple[str, object]]:
        merged: dict[str, tuple[str, object]] = {}
        for header in self.environment.spec.headers or []:
            merged[header.key.lower()] = (header.key, header.value)
        for key, raw in self._header_pairs(request_headers, engine):
            merged[key.lower()] = (key, raw)
        return [(key, engine.value(raw, f"headers.{key.lower()}")) for key, raw in merged.values()]

    def _header_pairs(self, request_headers: object, engine: Engine) -> list[tuple[str, object]]:
        node = request_headers
        if isinstance(node, dict):
            spot = hole(node)
            if spot is not None and spot[0] == "$val" and isinstance(spot[1], str):
                engine.trail.append(Trail("headers", Origin.INSTANCE, spot[1]))
                node = self._instance_value(spot[1])
        pairs: list[tuple[str, object]] = []
        if isinstance(node, list):
            for item in node:
                if isinstance(item, Header):  # list form: [{key, value}, ...]
                    pairs.append((item.key, item.value))
                elif isinstance(item, dict) and "key" in item:
                    pairs.append((str(item["key"]), item.get("value")))
        elif isinstance(node, dict) and hole(node) is None:
            # Mapping form: ``{Header-Name: value}`` (values still flow through the
            # engine in ``_headers``, so ``${...}``/``$secret`` resolve/mask).
            pairs.extend((str(key), value) for key, value in node.items())
        return pairs

    def _instance_value(self, identifier: str) -> object:
        instance = self.project.objects.get(identifier)
        return instance.spec.value if isinstance(instance, Instance) else None


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
