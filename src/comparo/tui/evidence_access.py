"""Bounded authenticated capture operations shared by both Analyze hosts."""

from dataclasses import dataclass
from pathlib import Path

import msgspec

from comparo.application import ArtifactBodyMessage
from comparo.application import ArtifactBodyReference
from comparo.application import ArtifactBodyWindow
from comparo.application import ArtifactReadLimits
from comparo.application import read_artifact_body_window
from comparo.application.curl_export import CurlExport
from comparo.application.curl_export import CurlExportLimits
from comparo.application.curl_export import CurlExportRequest
from comparo.application.curl_export import export_artifact_curl
from comparo.application.structured_bodies import StructuredBodyLimits
from comparo.application.structured_bodies import StructuredBodyPage
from comparo.application.structured_bodies import StructuredBodyQuery
from comparo.application.structured_bodies import read_artifact_structured_body_page

BODY_PAGE_BYTES = 64 * 1024
STRUCTURE_LIMITS = StructuredBodyLimits(
    maximum_document_bytes=4 * 1024 * 1024,
    maximum_nodes=50_000,
    maximum_depth=64,
    maximum_preview_characters=1024,
    maximum_pointer_bytes=2048,
    maximum_sse_events=10_000,
    maximum_sse_line_bytes=64 * 1024,
)


@dataclass(frozen=True)
class EvidenceAccess:
    """Fixed archive authority; no HTTP or live values are available here."""

    root: Path
    limits: ArtifactReadLimits

    async def body(self, reference: ArtifactBodyReference, offset: int) -> ArtifactBodyWindow:
        """Read one window without accumulating the complete capture in the UI."""
        return await read_artifact_body_window(
            self.root,
            reference.artifact_id,
            reference,
            self.limits,
            offset=offset,
            maximum_output_bytes=BODY_PAGE_BYTES,
        )

    async def structure(
        self, reference: ArtifactBodyReference, query: StructuredBodyQuery
    ) -> StructuredBodyPage:
        """Request one JSON or SSE page from the existing application service."""
        return await read_artifact_structured_body_page(
            self.root, reference.artifact_id, reference, self.limits, STRUCTURE_LIMITS, query
        )

    async def curl(self, reference: ArtifactBodyReference) -> CurlExport:
        """Export the authenticated request for the exact selected attempt/exchange."""
        return await export_artifact_curl(
            CurlExportRequest(
                root=self.root,
                bundle_id=reference.artifact_id,
                reference=msgspec.structs.replace(reference, message=ArtifactBodyMessage.REQUEST),
                artifact_limits=self.limits,
                export_limits=CurlExportLimits(
                    maximum_embedded_body_bytes=16 * 1024, maximum_command_characters=64 * 1024
                ),
            )
        )
