"""Complete finite runtime ceiling manifest used by Plan review."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.application.production_execution import ProductionExecutionSettings
from comparo.core.runtime.request_preparation import DEFAULT_REQUEST_TIMEOUT

_RUNTIME_LIMIT_IDENTITIES = frozenset(
    {
        *(("files", name) for name in ("bytes", "path-bytes", "segments")),
        *(
            ("environment", name)
            for name in ("files", "file-bytes", "total-bytes", "entries", "line-bytes")
        ),
        *(("yaml", name) for name in ("bytes", "depth", "nodes")),
        *(("body-store", name) for name in ("memory-threshold", "transient-bytes", "io-chunk")),
        *(("request-body", name) for name in ("bytes", "json-depth", "json-nodes")),
        *(
            ("http", name)
            for name in (
                "connections",
                "keepalive-connections",
                "authorization-occurrences",
                "keepalive-expiry",
            )
        ),
        *(
            ("capture", name)
            for name in ("encoded-body-bytes", "entity-body-bytes", "expansion-ratio")
        ),
        *(
            ("request-timeout", name)
            for name in (
                "connect",
                "tls-handshake",
                "response-headers",
                "idle",
                "attempt-total",
                "execution-total",
            )
        ),
        *(("streams", name) for name in ("records", "controls", "errors", "item-bytes")),
        *(
            ("ordinary", name)
            for name in ("body-bytes", "text-characters", "json-depth", "json-nodes", "json-tokens")
        ),
        *(
            ("evaluation", name)
            for name in (
                "selection-work",
                "selection-matches",
                "selected-result-bytes",
                "transform-work",
                "facts",
                "evidence-examples",
                "evidence-bytes",
                "derived-values",
                "derived-bytes",
                "pairs",
            )
        ),
        *(
            ("artifacts", name)
            for name in (
                "blobs",
                "blob-bytes",
                "total-blob-bytes",
                "manifest-bytes",
                "report-bytes",
                "io-chunk",
            )
        ),
        *(
            ("sanitization", name)
            for name in ("patterns", "pattern-bytes", "value-bytes", "value-nodes")
        ),
    }
)


@final
class RuntimeLimitInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One exact finite runtime ceiling with an explicit rational unit."""

    section: str
    name: str
    value: int
    unit: str
    denominator: int = 1

    def __post_init__(self) -> None:
        """Reject ambiguous, negative, or untyped limit values."""
        if any(type(item) is not str or not item for item in (self.section, self.name, self.unit)):
            raise ValueError("runtime limit identity and unit must be non-empty text")
        if type(self.value) is not int or self.value < 0:
            raise ValueError("runtime limit value must be a non-negative exact integer")
        if type(self.denominator) is not int or self.denominator < 1:
            raise ValueError("runtime limit denominator must be a positive exact integer")
        expected_unit = _runtime_limit_unit(self.section, self.name)
        if self.unit != expected_unit:
            raise ValueError("runtime limit unit contradicts its canonical identity")
        if self.unit != "ratio" and self.denominator != 1:
            raise ValueError("non-ratio runtime limits require denominator one")


@final
class RuntimeLimitsInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Complete finite runtime ceilings that will govern the admitted start."""

    limits: tuple[RuntimeLimitInspection, ...]
    artifact_persistence: str

    def __post_init__(self) -> None:
        """Require typed unique limits and one closed persistence mode."""
        if type(self.limits) is not tuple or any(
            type(item) is not RuntimeLimitInspection for item in self.limits
        ):
            raise TypeError("runtime limits must be a typed immutable tuple")
        identities = tuple((item.section, item.name) for item in self.limits)
        if len(identities) != len(set(identities)) or set(identities) != _RUNTIME_LIMIT_IDENTITIES:
            raise ValueError("runtime limits must cover the canonical v1 ceiling set exactly")
        if self.artifact_persistence not in {"always", "never", "on-gate-failure"}:
            raise ValueError("runtime artifact persistence mode is unsupported")
        if (
            self.find("http", "keepalive-connections").value
            > self.find("http", "connections").value
        ):
            raise ValueError("keepalive connections cannot exceed total connections")
        if (
            self.find("body-store", "memory-threshold").value
            > self.find("body-store", "transient-bytes").value
        ):
            raise ValueError("body-store memory threshold cannot exceed its aggregate ceiling")
        if (
            self.find("request-body", "bytes").value
            > self.find("body-store", "transient-bytes").value
        ):
            raise ValueError("request body ceiling cannot exceed aggregate transient bytes")
        if (
            self.find("ordinary", "body-bytes").value
            > self.find("capture", "entity-body-bytes").value
        ):
            raise ValueError("decoded body ceiling cannot exceed captured entity bytes")
        if (
            self.find("artifacts", "blob-bytes").value
            > self.find("artifacts", "total-blob-bytes").value
        ):
            raise ValueError("artifact blob ceiling cannot exceed aggregate artifact bytes")

    def find(self, section: str, name: str) -> RuntimeLimitInspection:
        """Return one exact ceiling by its stable presentation-independent identity."""
        try:
            return next(
                item for item in self.limits if item.section == section and item.name == name
            )
        except StopIteration as error:
            raise KeyError((section, name)) from error


def inspect_runtime_limits(settings: ProductionExecutionSettings) -> RuntimeLimitsInspection:
    """Project every value-free finite ceiling from effective execution settings."""
    if type(settings) is not ProductionExecutionSettings:
        raise TypeError("runtime limit inspection requires ProductionExecutionSettings")
    capture = settings.transport.capture
    artifacts = settings.artifact.limits
    sanitization = artifacts.sanitization
    return RuntimeLimitsInspection(
        limits=(
            _limit("files", "bytes", settings.files.maximum_bytes.value, "bytes"),
            _limit("files", "path-bytes", settings.files.maximum_path_bytes.value, "bytes"),
            _limit("files", "segments", settings.files.maximum_segments.value, "count"),
            _limit("environment", "files", settings.environment.maximum_files.value, "count"),
            _limit(
                "environment",
                "file-bytes",
                settings.environment.maximum_file_bytes.value,
                "bytes",
            ),
            _limit(
                "environment",
                "total-bytes",
                settings.environment.maximum_total_bytes.value,
                "bytes",
            ),
            _limit("environment", "entries", settings.environment.maximum_entries.value, "count"),
            _limit(
                "environment",
                "line-bytes",
                settings.environment.maximum_line_bytes.value,
                "bytes",
            ),
            _limit("yaml", "bytes", settings.yaml.maximum_bytes, "bytes"),
            _limit("yaml", "depth", settings.yaml.maximum_depth, "count"),
            _limit("yaml", "nodes", settings.yaml.maximum_nodes, "count"),
            _limit(
                "body-store",
                "memory-threshold",
                settings.body_store.memory_threshold.value,
                "bytes",
            ),
            _limit(
                "body-store",
                "transient-bytes",
                settings.body_store.maximum_transient_bytes.value,
                "bytes",
            ),
            _limit("body-store", "io-chunk", settings.body_store.io_chunk_size.value, "bytes"),
            _limit(
                "request-body",
                "bytes",
                settings.request_body.maximum_body_bytes.value,
                "bytes",
            ),
            _limit(
                "request-body",
                "json-depth",
                settings.request_body.maximum_json_depth.value,
                "count",
            ),
            _limit(
                "request-body",
                "json-nodes",
                settings.request_body.maximum_json_nodes.value,
                "count",
            ),
            _limit("http", "connections", settings.transport.maximum_connections.value, "count"),
            _limit(
                "http",
                "keepalive-connections",
                settings.transport.maximum_keepalive_connections.value,
                "count",
            ),
            _limit(
                "http",
                "authorization-occurrences",
                settings.transport.maximum_authorization_occurrences.value,
                "count",
            ),
            _limit(
                "http",
                "keepalive-expiry",
                settings.transport.keepalive_expiry.nanoseconds,
                "ns",
            ),
            _limit(
                "capture", "encoded-body-bytes", capture.maximum_encoded_body_bytes.value, "bytes"
            ),
            _limit(
                "capture", "entity-body-bytes", capture.maximum_entity_body_bytes.value, "bytes"
            ),
            _limit(
                "capture",
                "expansion-ratio",
                capture.maximum_expansion_ratio.numerator,
                "ratio",
                denominator=capture.maximum_expansion_ratio.denominator.value,
            ),
            *(
                _limit("request-timeout", name, DEFAULT_REQUEST_TIMEOUT.nanoseconds, "ns")
                for name in (
                    "connect",
                    "tls-handshake",
                    "response-headers",
                    "idle",
                    "attempt-total",
                    "execution-total",
                )
            ),
            _limit("streams", "records", settings.streams.maximum_records.value, "count"),
            _limit("streams", "controls", settings.streams.maximum_controls.value, "count"),
            _limit("streams", "errors", settings.streams.maximum_errors.value, "count"),
            _limit("streams", "item-bytes", settings.streams.maximum_item_bytes.value, "bytes"),
            _limit("ordinary", "body-bytes", settings.ordinary.maximum_body_bytes.value, "bytes"),
            _limit(
                "ordinary",
                "text-characters",
                settings.ordinary.maximum_text_characters.value,
                "count",
            ),
            _limit("ordinary", "json-depth", settings.ordinary.maximum_json_depth.value, "count"),
            _limit("ordinary", "json-nodes", settings.ordinary.maximum_json_nodes.value, "count"),
            _limit("ordinary", "json-tokens", settings.ordinary.maximum_json_tokens.value, "count"),
            *(
                _limit("evaluation", name, value, unit)
                for name, value, unit in (
                    ("selection-work", settings.evaluation.selection_work, "count"),
                    ("selection-matches", settings.evaluation.selection_matches, "count"),
                    ("selected-result-bytes", settings.evaluation.selected_result_bytes, "bytes"),
                    ("transform-work", settings.evaluation.transform_work, "count"),
                    ("facts", settings.evaluation.facts, "count"),
                    ("evidence-examples", settings.evaluation.evidence_examples, "count"),
                    ("evidence-bytes", settings.evaluation.evidence_bytes, "bytes"),
                    ("derived-values", settings.evaluation.derived_values, "count"),
                    ("derived-bytes", settings.evaluation.derived_bytes, "bytes"),
                    ("pairs", settings.evaluation.pairs, "count"),
                )
            ),
            _limit("artifacts", "blobs", artifacts.maximum_blobs.value, "count"),
            _limit("artifacts", "blob-bytes", artifacts.maximum_blob_bytes.value, "bytes"),
            _limit(
                "artifacts", "total-blob-bytes", artifacts.maximum_total_blob_bytes.value, "bytes"
            ),
            _limit("artifacts", "manifest-bytes", artifacts.maximum_manifest_bytes.value, "bytes"),
            _limit("artifacts", "report-bytes", artifacts.maximum_report_bytes.value, "bytes"),
            _limit("sanitization", "patterns", sanitization.maximum_patterns.value, "count"),
            _limit(
                "sanitization",
                "pattern-bytes",
                sanitization.maximum_pattern_bytes.value,
                "bytes",
            ),
            _limit("sanitization", "value-bytes", sanitization.maximum_value_bytes.value, "bytes"),
            _limit("sanitization", "value-nodes", sanitization.maximum_value_nodes.value, "count"),
            _limit("artifacts", "io-chunk", settings.artifact_io_chunk_size.value, "bytes"),
        ),
        artifact_persistence=settings.artifact.persist.value,
    )


def _limit(
    section: str,
    name: str,
    value: int,
    unit: str,
    *,
    denominator: int = 1,
) -> RuntimeLimitInspection:
    return RuntimeLimitInspection(
        section=section,
        name=name,
        value=value,
        unit=unit,
        denominator=denominator,
    )


def _runtime_limit_unit(section: str, name: str) -> str:
    if section == "request-timeout" or (section, name) == ("http", "keepalive-expiry"):
        return "ns"
    if (section, name) == ("capture", "expansion-ratio"):
        return "ratio"
    if "bytes" in name or name in {"io-chunk", "memory-threshold"}:
        return "bytes"
    return "count"


__all__ = (
    "RuntimeLimitInspection",
    "RuntimeLimitsInspection",
    "inspect_runtime_limits",
)
