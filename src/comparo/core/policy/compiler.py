"""Pure source-located compiler for the operator-owned ExecutionPolicy language."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import cast

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import SourceLocation
from comparo.core.policy.models import PLANNING_RESOURCES
from comparo.core.policy.models import POLICY_API_VERSION
from comparo.core.policy.models import AmbientEnvironmentPolicy
from comparo.core.policy.models import CompiledExecutionPolicy
from comparo.core.policy.models import NetworkAddressClass
from comparo.core.policy.models import PolicyArtifacts
from comparo.core.policy.models import PolicyCompilationResult
from comparo.core.policy.models import PolicyHttp
from comparo.core.policy.models import PolicyPlanning
from comparo.core.policy.models import PolicyResourceSelector
from comparo.core.policy.models import PolicyValues
from comparo.core.policy.models import RemoteSchemaPolicy

_DEFAULT_NAMESPACE = NamespaceId(value="operator")
_RESOURCE_REFERENCE = re.compile(r"(Request|Scenario|Environment)/([a-z0-9-]+)")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WINDOWS_ABSOLUTE = re.compile(r"[A-Z]:/")

_DEFAULT_CEILING_VALUES: dict[BudgetResource, int] = {
    BudgetResource.REQUESTS: 10_000,
    BudgetResource.MATRIX_CELLS: 100_000,
    BudgetResource.TRIALS: 1_000,
    BudgetResource.ENVIRONMENT_EXECUTIONS: 100_000,
    BudgetResource.ATTEMPTS: 300_000,
    BudgetResource.COMPARISONS: 100_000,
    BudgetResource.QUEUED_WORK: 64,
    BudgetResource.CONCURRENCY: 8,
}


def default_planning_ceilings() -> tuple[PlanningBudgetValue, ...]:
    """Return fresh immutable policy-version defaults for every planning dimension."""
    return tuple(
        PlanningBudgetValue(
            resource=resource,
            limit=PositiveCount(value=_DEFAULT_CEILING_VALUES[resource]),
        )
        for resource in PLANNING_RESOURCES
    )


class _PolicyCompiler:
    """Aggregate safe diagnostics while normalizing one exact policy document."""

    __slots__ = ("_diagnostics", "_fallback_span", "_namespace")

    def __init__(self, namespace: NamespaceId) -> None:
        self._namespace = namespace
        self._diagnostics: list[ConfigurationDiagnostic] = []
        self._fallback_span: SourceSpan | None = None

    def compile(self, document: ParsedDocument) -> PolicyCompilationResult:
        """Compile one parsed document or return every discovered safe diagnostic."""
        self._fallback_span = document.root.span
        root = self._object(
            document.root,
            prefix="policy.envelope",
            allowed={"apiVersion", "kind", "metadata", "spec"},
            required={"apiVersion", "kind", "metadata", "spec"},
        )
        if root is None:
            return PolicyCompilationResult(diagnostics=tuple(self._diagnostics))
        version = self._text(root.get("apiVersion"), code="policy.envelope.api-version")
        kind = self._text(root.get("kind"), code="policy.envelope.kind")
        if version is not None and version != POLICY_API_VERSION:
            self._add("policy.envelope.api-version", root["apiVersion"].span)
        if kind is not None and kind != ResourceKind.EXECUTION_POLICY.value:
            self._add("policy.envelope.kind", root["kind"].span)
        metadata = self._metadata(root.get("metadata"))
        sections = self._spec(root.get("spec"))
        if self._diagnostics:
            return PolicyCompilationResult(diagnostics=tuple(self._diagnostics))
        if metadata is None or sections is None:  # pragma: no cover - diagnostic invariant
            raise RuntimeError("policy compiler lost a diagnostic")
        resource_id, descriptive = metadata
        planning, http, values, artifacts = sections
        identity = ResourceIdentity(
            namespace=self._namespace,
            kind=ResourceKind.EXECUTION_POLICY,
            resource_id=resource_id,
        )
        fingerprint = policy_fingerprint(
            identity,
            descriptive,
            planning,
            http,
            values,
            artifacts,
        )
        return PolicyCompilationResult(
            policy=CompiledExecutionPolicy(
                api_version=POLICY_API_VERSION,
                definition=DefinitionRef(identity=identity, fingerprint=fingerprint),
                metadata=descriptive,
                location=document.root.span.start,
                planning=planning,
                http=http,
                values=values,
                artifacts=artifacts,
            )
        )

    def _metadata(self, node: ConfigurationNode | None) -> tuple[ResourceId, Metadata] | None:
        fields = self._object(
            node,
            prefix="policy.metadata",
            allowed={"id", "name", "description", "tags"},
            required={"id", "name"},
        )
        if fields is None:
            return None
        resource_id_text = self._text(fields.get("id"), code="policy.metadata.id")
        name = self._nonblank_text(fields.get("name"), code="policy.metadata.name")
        description = (
            None
            if "description" not in fields
            else self._nonblank_text(
                fields["description"],
                code="policy.metadata.description",
            )
        )
        tags = (
            ()
            if "tags" not in fields
            else self._string_list(
                fields["tags"],
                code="policy.metadata.tags",
                validator=_valid_slug,
            )
        )
        resource_id: ResourceId | None = None
        if resource_id_text is not None:
            try:
                resource_id = ResourceId(value=resource_id_text)
            except ValueError:
                self._add("policy.metadata.id", fields["id"].span)
        if resource_id is None or name is None or tags is None:
            return None
        return resource_id, Metadata(name=name, description=description, tags=tags)

    def _spec(
        self,
        node: ConfigurationNode | None,
    ) -> tuple[PolicyPlanning, PolicyHttp, PolicyValues, PolicyArtifacts] | None:
        fields = self._object(
            node,
            prefix="policy.spec",
            allowed={"planning", "http", "values", "artifacts"},
            required={"planning", "http", "values", "artifacts"},
        )
        if fields is None:
            return None
        planning = self._planning(fields.get("planning"))
        http = self._http(fields.get("http"))
        values = self._values(fields.get("values"))
        artifacts = self._artifacts(fields.get("artifacts"))
        if any(value is None for value in (planning, http, values, artifacts)):
            return None
        return (
            cast("PolicyPlanning", planning),
            cast("PolicyHttp", http),
            cast("PolicyValues", values),
            cast("PolicyArtifacts", artifacts),
        )

    def _planning(self, node: ConfigurationNode | None) -> PolicyPlanning | None:
        fields = self._object(
            node,
            prefix="policy.planning",
            allowed={
                "subjects",
                "environments",
                "allowAnySubjects",
                "allowAnyEnvironments",
                "ceilings",
            },
            required={
                "subjects",
                "environments",
                "allowAnySubjects",
                "allowAnyEnvironments",
            },
        )
        if fields is None:
            return None
        subjects = self._selectors(fields.get("subjects"), subject=True)
        environments = self._selectors(fields.get("environments"), subject=False)
        allow_any_subjects = self._boolean(
            fields.get("allowAnySubjects"),
            code="policy.planning.flag",
        )
        allow_any_environments = self._boolean(
            fields.get("allowAnyEnvironments"),
            code="policy.planning.flag",
        )
        ceilings = self._ceilings(fields.get("ceilings"))
        if any(
            value is None
            for value in (subjects, environments, allow_any_subjects, allow_any_environments)
        ):
            return None
        if allow_any_subjects and subjects:
            self._add("policy.planning.subject-conflict", fields["subjects"].span)
        if allow_any_environments and environments:
            self._add("policy.planning.environment-conflict", fields["environments"].span)
        if self._diagnostics:
            return None
        return PolicyPlanning(
            subjects=cast("tuple[PolicyResourceSelector, ...]", subjects),
            environments=cast("tuple[PolicyResourceSelector, ...]", environments),
            allow_any_subjects=cast("bool", allow_any_subjects),
            allow_any_environments=cast("bool", allow_any_environments),
            ceilings=ceilings,
        )

    def _selectors(
        self,
        node: ConfigurationNode | None,
        *,
        subject: bool,
    ) -> tuple[PolicyResourceSelector, ...] | None:
        values = self._string_list(
            node,
            code="policy.planning.selector",
            validator=lambda value: _valid_reference(value, subject=subject),
        )
        if values is None:
            return None
        selectors = tuple(_selector(value) for value in values)
        return tuple(sorted(selectors, key=lambda item: (item.kind.value, item.resource_id.value)))

    def _ceilings(self, node: ConfigurationNode | None) -> tuple[PlanningBudgetValue, ...]:
        defaults = dict(_DEFAULT_CEILING_VALUES)
        if node is None:
            return default_planning_ceilings()
        fields = self._object(
            node,
            prefix="policy.planning.ceilings",
            allowed={resource.value for resource in PLANNING_RESOURCES},
            required=set(),
        )
        if fields is None:
            return default_planning_ceilings()
        for name, value_node in fields.items():
            if name not in {resource.value for resource in PLANNING_RESOURCES}:
                continue
            value = self._positive_integer(value_node, code="policy.planning.ceiling")
            if value is not None:
                defaults[BudgetResource(name)] = value
        return tuple(
            PlanningBudgetValue(
                resource=resource,
                limit=PositiveCount(value=defaults[resource]),
            )
            for resource in PLANNING_RESOURCES
        )

    def _http(self, node: ConfigurationNode | None) -> PolicyHttp | None:
        required = {
            "allowedSchemes",
            "allowedMethods",
            "allowedHosts",
            "allowedPorts",
            "allowedAddressClasses",
            "allowAnyHost",
            "requireMutationApproval",
            "allowProxyDns",
        }
        fields = self._object(
            node,
            prefix="policy.http",
            allowed=required,
            required=required,
        )
        if fields is None:
            return None
        schemes = self._enum_list(
            fields.get("allowedSchemes"),
            HttpScheme,
            code="policy.http.scheme",
        )
        methods = self._methods(fields.get("allowedMethods"))
        hosts = self._hosts(fields.get("allowedHosts"))
        ports = self._ports(fields.get("allowedPorts"))
        address_classes = self._enum_list(
            fields.get("allowedAddressClasses"),
            NetworkAddressClass,
            code="policy.http.address-class",
        )
        allow_any_host = self._boolean(fields.get("allowAnyHost"), code="policy.http.flag")
        require_mutation = self._boolean(
            fields.get("requireMutationApproval"),
            code="policy.http.flag",
        )
        allow_proxy_dns = self._boolean(fields.get("allowProxyDns"), code="policy.http.flag")
        if any(
            value is None
            for value in (
                schemes,
                methods,
                hosts,
                ports,
                address_classes,
                allow_any_host,
                require_mutation,
                allow_proxy_dns,
            )
        ):
            return None
        if not schemes:
            self._add("policy.http.scheme", fields["allowedSchemes"].span)
        if not methods:
            self._add("policy.http.method", fields["allowedMethods"].span)
        if not ports:
            self._add("policy.http.port", fields["allowedPorts"].span)
        if not address_classes:
            self._add("policy.http.address-class", fields["allowedAddressClasses"].span)
        if allow_any_host and hosts:
            self._add("policy.http.host-conflict", fields["allowedHosts"].span)
        if self._diagnostics:
            return None
        return PolicyHttp(
            allowed_schemes=cast("tuple[HttpScheme, ...]", schemes),
            allowed_methods=cast("tuple[HttpMethod, ...]", methods),
            allowed_hosts=cast("tuple[HttpHost, ...]", hosts),
            allowed_ports=cast("tuple[HttpPort, ...]", ports),
            allowed_address_classes=cast(
                "tuple[NetworkAddressClass, ...]",
                address_classes,
            ),
            allow_any_host=cast("bool", allow_any_host),
            require_mutation_approval=cast("bool", require_mutation),
            allow_proxy_dns=cast("bool", allow_proxy_dns),
        )

    def _values(self, node: ConfigurationNode | None) -> PolicyValues | None:
        required = {"ambientEnvironment", "allowProjectFiles", "remoteSchemas"}
        fields = self._object(
            node,
            prefix="policy.values",
            allowed=required,
            required=required,
        )
        if fields is None:
            return None
        ambient = self._ambient_environment(fields.get("ambientEnvironment"))
        project_files = self._boolean(
            fields.get("allowProjectFiles"),
            code="policy.values.project-files",
        )
        remote = self._remote_schemas(fields.get("remoteSchemas"))
        if ambient is None or project_files is None or remote is None:
            return None
        return PolicyValues(
            ambient_environment=ambient,
            allow_project_files=project_files,
            remote_schemas=remote,
        )

    def _ambient_environment(
        self,
        node: ConfigurationNode | None,
    ) -> AmbientEnvironmentPolicy | None:
        fields = self._object(
            node,
            prefix="policy.values.ambient-environment",
            allowed={"names", "allowAny"},
            required={"names", "allowAny"},
        )
        if fields is None:
            return None
        names = self._string_list(
            fields.get("names"),
            code="policy.values.environment-name",
            validator=lambda value: (
                len(value) <= 255 and _ENVIRONMENT_NAME.fullmatch(value) is not None
            ),
        )
        allow_any = self._boolean(
            fields.get("allowAny"),
            code="policy.values.environment-flag",
        )
        if names is None or allow_any is None:
            return None
        if allow_any and names:
            self._add("policy.values.environment-conflict", fields["names"].span)
            return None
        return AmbientEnvironmentPolicy(names=names, allow_any=allow_any)

    def _remote_schemas(self, node: ConfigurationNode | None) -> RemoteSchemaPolicy | None:
        fields = self._object(
            node,
            prefix="policy.values.remote-schemas",
            allowed={"sources", "allowAny"},
            required={"sources", "allowAny"},
        )
        if fields is None:
            return None
        sources = self._string_list(
            fields.get("sources"),
            code="policy.values.schema-source",
            validator=_valid_schema_source,
        )
        allow_any = self._boolean(
            fields.get("allowAny"),
            code="policy.values.schema-flag",
        )
        if sources is None or allow_any is None:
            return None
        if allow_any and sources:
            self._add("policy.values.schema-conflict", fields["sources"].span)
            return None
        return RemoteSchemaPolicy(
            sources=tuple(
                NamedCapabilityTarget(
                    kind=CapabilityTargetKind.SCHEMA_SOURCE,
                    identifier=source,
                )
                for source in sources
            ),
            allow_any=allow_any,
        )

    def _artifacts(self, node: ConfigurationNode | None) -> PolicyArtifacts | None:
        fields = self._object(
            node,
            prefix="policy.artifacts",
            allowed={"allowSanitizedPersistence", "roots"},
            required={"allowSanitizedPersistence", "roots"},
        )
        if fields is None:
            return None
        allowed = self._boolean(
            fields.get("allowSanitizedPersistence"),
            code="policy.artifacts.permission",
        )
        roots = self._string_list(
            fields.get("roots"),
            code="policy.artifacts.root",
            validator=_valid_absolute_root,
        )
        if allowed is None or roots is None:
            return None
        if allowed != bool(roots):
            self._add("policy.artifacts.root-conflict", fields["roots"].span)
            return None
        return PolicyArtifacts(allow_sanitized_persistence=allowed, roots=roots)

    def _methods(self, node: ConfigurationNode | None) -> tuple[HttpMethod, ...] | None:
        values = self._string_list(
            node,
            code="policy.http.method",
            validator=_valid_method,
        )
        if values is None:
            return None
        return tuple(HttpMethod(value=value) for value in values)

    def _hosts(self, node: ConfigurationNode | None) -> tuple[HttpHost, ...] | None:
        values = self._string_list(
            node,
            code="policy.http.host",
            validator=_valid_host,
        )
        if values is None:
            return None
        hosts = tuple(_host(value) for value in values)
        return tuple(sorted(hosts, key=lambda item: (type(item).__name__, item.value)))

    def _ports(self, node: ConfigurationNode | None) -> tuple[HttpPort, ...] | None:
        if type(node) is not SequenceNode:
            self._add("policy.http.port", self._span(node))
            return None
        ports: list[HttpPort] = []
        valid = True
        for item in node.items:
            if type(item) is not ScalarNode or type(item.value) is not int:
                self._add("policy.http.port", item.span)
                valid = False
                continue
            try:
                ports.append(HttpPort(value=item.value))
            except (TypeError, ValueError):
                self._add("policy.http.port", item.span)
                valid = False
        if not valid:
            return None
        if len(ports) != len(set(ports)):
            self._add("policy.value.duplicate", node.span)
            return None
        return tuple(sorted(ports, key=lambda item: item.value))

    def _enum_list[EnumT: DomainStrEnum](
        self,
        node: ConfigurationNode | None,
        enum_type: type[EnumT],
        *,
        code: str,
    ) -> tuple[EnumT, ...] | None:
        values = self._string_list(
            node,
            code=code,
            validator=lambda value: _is_enum_value(enum_type, value),
        )
        if values is None:
            return None
        return tuple(sorted((enum_type(value) for value in values), key=lambda item: item.value))

    def _object(
        self,
        node: ConfigurationNode | None,
        *,
        prefix: str,
        allowed: set[str],
        required: set[str],
    ) -> dict[str, ConfigurationNode] | None:
        if type(node) is not MappingNode:
            self._add(f"{prefix}.mapping", self._span(node))
            return None
        fields = {entry.name: entry.value for entry in node.entries}
        for entry in node.entries:
            if entry.name not in allowed:
                self._add(f"{prefix}.field-unknown", entry.name_span)
        for _name in sorted(required - fields.keys()):
            self._add(f"{prefix}.field-required", node.span)
        return fields

    def _text(self, node: ConfigurationNode | None, *, code: str) -> str | None:
        if type(node) is not ScalarNode or type(node.value) is not str:
            self._add(code, self._span(node))
            return None
        return node.value

    def _nonblank_text(self, node: ConfigurationNode | None, *, code: str) -> str | None:
        value = self._text(node, code=code)
        if value is not None and not value.strip():
            self._add(code, cast("ScalarNode", node).span)
            return None
        return value

    def _boolean(self, node: ConfigurationNode | None, *, code: str) -> bool | None:
        if type(node) is not ScalarNode or type(node.value) is not bool:
            self._add(code, self._span(node))
            return None
        return node.value

    def _positive_integer(self, node: ConfigurationNode, *, code: str) -> int | None:
        if type(node) is not ScalarNode or type(node.value) is not int or node.value < 1:
            self._add(code, node.span)
            return None
        return node.value

    def _string_list(
        self,
        node: ConfigurationNode | None,
        *,
        code: str,
        validator: Callable[[str], bool],
    ) -> tuple[str, ...] | None:
        if type(node) is not SequenceNode:
            self._add(code, self._span(node))
            return None
        values: list[str] = []
        valid = True
        for item in node.items:
            if (
                type(item) is not ScalarNode
                or type(item.value) is not str
                or not validator(item.value)
            ):
                self._add(code, item.span)
                valid = False
            else:
                values.append(item.value)
        if not valid:
            return None
        if len(values) != len(set(values)):
            self._add("policy.value.duplicate", node.span)
            return None
        return tuple(sorted(values))

    def _add(self, code: str, span: SourceSpan) -> None:
        self._diagnostics.append(
            ConfigurationDiagnostic(
                code=DiagnosticCode(value=code),
                severity=DiagnosticSeverity.ERROR,
                primary=span,
            )
        )

    def _span(self, node: ConfigurationNode | None) -> SourceSpan:
        if node is not None:
            return node.span
        if self._fallback_span is None:  # pragma: no cover - compile initializes it
            raise RuntimeError("policy compiler has no source span")
        return self._fallback_span


def compile_execution_policy(
    documents: tuple[ParsedDocument, ...],
    *,
    namespace: NamespaceId = _DEFAULT_NAMESPACE,
    source_id: SourceId,
    diagnostics: tuple[ConfigurationDiagnostic, ...] = (),
) -> PolicyCompilationResult:
    """Compile exactly one explicit parsed policy document without performing I/O."""
    if type(documents) is not tuple or any(type(item) is not ParsedDocument for item in documents):
        raise TypeError("policy compilation documents must be immutable and typed")
    if type(namespace) is not NamespaceId or type(source_id) is not SourceId:
        raise TypeError("policy compilation requires typed namespace and source identity")
    if type(diagnostics) is not tuple or any(
        type(item) is not ConfigurationDiagnostic for item in diagnostics
    ):
        raise TypeError("policy compilation diagnostics must be immutable and typed")
    if diagnostics:
        return PolicyCompilationResult(diagnostics=diagnostics)
    if len(documents) != 1:
        span = documents[1].root.span if len(documents) > 1 else _initial_span(source_id)
        return PolicyCompilationResult(
            diagnostics=(
                ConfigurationDiagnostic(
                    code=DiagnosticCode(value="policy.document.exactly-one"),
                    severity=DiagnosticSeverity.ERROR,
                    primary=span,
                ),
            )
        )
    return _PolicyCompiler(namespace).compile(documents[0])


def policy_fingerprint(
    identity: ResourceIdentity,
    metadata: Metadata,
    planning: PolicyPlanning,
    http: PolicyHttp,
    values: PolicyValues,
    artifacts: PolicyArtifacts,
) -> Fingerprint:
    """Hash one complete normalized policy without adapter or source-path state."""
    content = {
        "apiVersion": POLICY_API_VERSION,
        "identity": {
            "namespace": identity.namespace.value,
            "kind": identity.kind.value,
            "id": identity.resource_id.value,
        },
        "metadata": {
            "name": metadata.name,
            "description": metadata.description,
            "tags": list(metadata.tags),
        },
        "planning": {
            "subjects": _selectors_json(planning.subjects),
            "environments": _selectors_json(planning.environments),
            "allowAnySubjects": planning.allow_any_subjects,
            "allowAnyEnvironments": planning.allow_any_environments,
            "ceilings": {item.resource.value: item.limit.value for item in planning.ceilings},
        },
        "http": {
            "allowedSchemes": [item.value for item in http.allowed_schemes],
            "allowedMethods": [item.value for item in http.allowed_methods],
            "allowedHosts": [item.value for item in http.allowed_hosts],
            "allowedPorts": [item.value for item in http.allowed_ports],
            "allowedAddressClasses": [item.value for item in http.allowed_address_classes],
            "allowAnyHost": http.allow_any_host,
            "requireMutationApproval": http.require_mutation_approval,
            "allowProxyDns": http.allow_proxy_dns,
        },
        "values": {
            "ambientEnvironment": {
                "names": list(values.ambient_environment.names),
                "allowAny": values.ambient_environment.allow_any,
            },
            "allowProjectFiles": values.allow_project_files,
            "remoteSchemas": {
                "sources": [item.identifier for item in values.remote_schemas.sources],
                "allowAny": values.remote_schemas.allow_any,
            },
        },
        "artifacts": {
            "allowSanitizedPersistence": artifacts.allow_sanitized_persistence,
            "roots": list(artifacts.roots),
        },
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(encoded).hexdigest()),
    )


def _selectors_json(selectors: tuple[PolicyResourceSelector, ...]) -> list[str]:
    return [f"{item.kind.value}/{item.resource_id.value}" for item in selectors]


def _selector(value: str) -> PolicyResourceSelector:
    match = _RESOURCE_REFERENCE.fullmatch(value)
    if match is None:  # pragma: no cover - validated by caller
        raise ValueError("invalid policy selector")
    return PolicyResourceSelector(
        kind=ResourceKind(match.group(1)),
        resource_id=ResourceId(value=match.group(2)),
    )


def _valid_reference(value: str, *, subject: bool) -> bool:
    match = _RESOURCE_REFERENCE.fullmatch(value)
    if match is None:
        return False
    kind = ResourceKind(match.group(1))
    if kind not in (
        {ResourceKind.REQUEST, ResourceKind.SCENARIO} if subject else {ResourceKind.ENVIRONMENT}
    ):
        return False
    try:
        ResourceId(value=match.group(2))
    except ValueError:
        return False
    return True


def _valid_slug(value: str) -> bool:
    try:
        ResourceId(value=value)
    except ValueError:
        return False
    return True


def _valid_method(value: str) -> bool:
    try:
        HttpMethod(value=value)
    except ValueError:
        return False
    return True


def _valid_host(value: str) -> bool:
    try:
        _host(value)
    except ValueError:
        return False
    return True


def _host(value: str) -> HttpHost:
    for host_type in (Ipv4Host, Ipv6Host, DnsHost):
        try:
            return host_type(value=value)
        except ValueError:
            continue
    raise ValueError("invalid policy host")


def _valid_schema_source(value: str) -> bool:
    if not value.startswith(("https://", "http://")):
        return False
    try:
        NamedCapabilityTarget(
            kind=CapabilityTargetKind.SCHEMA_SOURCE,
            identifier=value,
        )
    except ValueError:
        return False
    return True


def _valid_absolute_root(value: str) -> bool:
    if not value or len(value) > 4096:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if value == "/" or _WINDOWS_ABSOLUTE.fullmatch(value) is not None:
        return True
    if value.startswith("/"):
        remainder = value[1:]
    elif _WINDOWS_ABSOLUTE.match(value) is not None:
        remainder = value[3:]
    else:
        return False
    return not value.endswith("/") and all(
        segment not in {"", ".", ".."} for segment in remainder.split("/")
    )


def _is_enum_value[EnumT: DomainStrEnum](enum_type: type[EnumT], value: str) -> bool:
    try:
        enum_type(value)
    except ValueError:
        return False
    return True


def _initial_span(source_id: SourceId) -> SourceSpan:
    location = SourceLocation(
        source_id=source_id,
        document=PositiveCount(value=1),
        line=PositiveCount(value=1),
        column=PositiveCount(value=1),
    )
    return SourceSpan(start=location, end=location)


__all__ = (
    "compile_execution_policy",
    "default_planning_ceilings",
    "policy_fingerprint",
)
