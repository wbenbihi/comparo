# Security Policy

comparo handles credentials and can capture HTTP responses, so security reports are taken
seriously.

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Instead, use GitHub's
[private vulnerability reporting](https://github.com/wbenbihi/comparo/security/advisories/new)
for this repository. You will receive an acknowledgement, and we will coordinate a fix and
disclosure timeline with you.

## Handling of secrets

The current `refactor` branch ships an immutable domain foundation only. It does not resolve
configuration, execute HTTP requests, render a TUI, write logs or artifacts, or persist snapshots.
Claims about runtime redaction or sanitized artifacts therefore do not apply yet.

Step 1 provides the containment rules later adapters must preserve:

- Live secret or tainted payloads use the exact sealed `SensitiveValue` carrier, which refuses
  generic rendering, copying, pickling, and msgspec serialization.
- Secret literals are sealed at the expression boundary; sensitive digests, HTTP values, decoded
  handles, and selected values cannot silently downgrade to public values.
- Diagnostics and provenance accept only constrained identifiers and typed, value-free context.
- External analysis and outbound HTTP effects require exact, fingerprinted policy authority bound
  to the complete request occurrence.

This protects accidental use through the supported domain API; it is not a sandbox against hostile
Python code in the same process. Runtime resolution, output sanitization, artifact compilation, and
UI redaction remain future milestones and must be independently audited before public execution is
enabled.

If you find a way to extract a contained value through an ordinary domain operation, serialize it,
or place payload-bearing data in a value-free diagnostic or authority record, please report it as a
vulnerability. Once runtime and artifact layers exist, terminal, log, report, snapshot, and version
control leaks will also be treated as vulnerabilities.
