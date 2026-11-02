# Report and artifact formats

Current working-tree contracts, reconciled **2026-10-31**:

| Contract | Version | Purpose |
| --- | --- | --- |
| Configuration | `comparo/v1alpha1` | Authored project resources |
| Execution artifact | `comparo.artifact/v1alpha1` | Immutable sanitized terminal run, facts, coverage and capture descriptors |
| JSON report envelope | `comparo.report/v1alpha1` | Machine-readable exported report containing the artifact |

These versions are independent. The unsupported prototype format is preserved in the
[historical reference](history/report-format-prototype-v1.md). Generate exact current schemas:

```console
uv run --frozen comparo schema artifact
uv run --frozen comparo schema report
```

## Stored bundle

The POSIX local store publishes a private bundle under the selected artifact root:

```text
.comparo/artifacts/<bundle-id>/
  COMPLETE
  manifest.json
  results.json
  blobs/
```

`results.json` contains the canonical sanitized RunArtifact; `manifest.json` and the completion
marker participate in integrity verification. Detached sanitized bodies are declared by
content-addressed descriptors. Artifact records never contain live sensitive values, transient body
handles, exceptions or arbitrary storage paths. The reader authenticates identities/digests and
validates cross-record lineage and counts; JSON Schema alone is not sufficient validation.

The artifact retains Project/policy/profile provenance, configuration/plan fingerprints, completion
and gate state, executions/attempts/exchanges, assertions, comparisons/diffs, timings, coverage and
budgets. Missing measurements, unavailable exchanges, redaction, omission and truncation are explicit.
Sanitized does not mean that every business-sensitive public field has been removed.

See [artifact ownership, sanitization and persistence](artifacts-and-reporters.md) for the exact
lifecycle, permissions, verification and platform guarantees. Linux/macOS local POSIX paths are
supported; Windows persistence and network-filesystem durability are not claimed.

## Exported reports

Reporters consume the sanitized artifact, never live execution objects:

- **JSON:** a strict `comparo.report/v1alpha1` envelope around the complete artifact.
- **JUnit XML:** gate-oriented CI results.
- **SARIF 2.1.0:** adverse facts and incomplete coverage with stable finding identities.
- **Markdown:** bounded overview, gate, coverage, execution, comparison, finding and budget sections.

Report generation does not automatically embed detached body bytes. Results remain authoritative
when reopened; neither the TUI nor a reporter recomputes assertions, diffs or the gate.

Replace `BUNDLE_ID` with an ID from the selected archive:

```console
uv run --frozen comparo artifact inspect BUNDLE_ID --root examples/quickstart/.comparo/artifacts
uv run --frozen comparo artifact verify BUNDLE_ID --root examples/quickstart/.comparo/artifacts
uv run --frozen comparo report BUNDLE_ID --root examples/quickstart/.comparo/artifacts --format markdown
```

## TUI inspection and limits

Reports searches and filters a bounded authenticated listing, previews outcomes and opens the same
Analyze view as Launch. Export explicitly selects format and destination without replacing an
existing file. Opening a report does not resend Requests.

Captured HTTP messages have exact artifact/digest/execution/attempt/exchange references. The shared
inspector reads raw 64 KiB windows, JSON subtrees and SSE event pages through application APIs;
`i` opens it and Escape restores Analyze. Structured reads are capped at 4 MiB; use the raw pager
for larger captures within artifact read limits. Reviewed cURL reconstructs only the stored sanitized
request and declares replay limitations. It does not reproduce Comparo's validation, authorization,
retry or gate behavior, and does not restore live credentials.

Body bytes are available after a run only if persistence succeeded and capture policy retained them.
Non-persisted runs retain result metadata but lose temporary detached-body storage during cleanup.
Retention/trash/restore and cursor-based large-archive queries exist in backend APIs but are not yet
exposed as Reports controls. See [Reports](codebase/tui-reports.md),
[capture controls](codebase/tui-launch.md#full-capture-inspection) and
[application APIs](codebase/application-api-reference.md).
