# Security Policy

Comparo resolves credentials, executes HTTP requests and can persist sanitized captures. This
policy describes the rebuilt `comparo/v1alpha1` working tree as of **2026-10-31**. It remains alpha;
see [current status](docs/status.md) and [CLI limitations](docs/cli.md#current-executable-subset).

## Reporting a vulnerability

Please do not open a public issue containing credentials, captures or exploit details. Use GitHub's
[private vulnerability reporting](https://github.com/wbenbihi/comparo/security/advisories/new).
We will coordinate acknowledgement, remediation and disclosure with you.

## Authority and execution

The local CLI and TUI default to explicit, permissive trusted-local authority when `--policy` is
omitted. For untrusted project configuration or CI, supply an operator-owned ExecutionPolicy from
outside project discovery. A project cannot grant or weaken that authority. Mutation approval is
separate from permission; unsupported capabilities fail before external effects.

HTTP destinations, resolved addresses, redirects and destination-bound secret egress are checked
through the production authorization path. Local artifact/value adapters require POSIX facilities;
Linux and macOS are supported for these paths. Comparo does not claim to sandbox hostile Python
code running in the same process.

## Live values and sanitized outputs

- Live sensitive values use sealed carriers that reject generic rendering and serialization.
  Sensitivity propagates through derived values; provider/adapter exceptions are not UI messages.
- Runtime resolution and capture are implemented. The artifact compiler applies mandatory
  sanitization before persistence; reports and TUI analysis consume the sanitized artifact rather
  than live observations. Missing, partial, omitted and redacted captures remain explicit.
- Persisted manifests and blobs are authenticated by the application readers. Body references
  bind artifact identity, canonical result digest, execution, attempt and exchange coordinates.
- Masked source/resolution views are distinct from explicit trusted-exact source access. Exact
  source inspection can disclose authored secrets and must never be treated as a sanitized view.
- Sanitized data can still contain business-sensitive public fields. cURL export uses stored
  sanitized requests, declares replay limitations and requires explicit copy in the TUI. Its
  preview expires; copied content remains in the user-controlled system clipboard.

## Local writes

Run persistence follows reviewed execution settings and capture policy. Report export and manual
ExecutionProfile saving use explicit destinations and do not overwrite existing source/export
files. Profile saving does not persist resolved values or mutation approval. TUI preferences are a
separate user-owned JSON file written only on Save; malformed saved preferences are not rewritten
at startup. Browse-only report access does not create an archive.

Report unintended disclosure through supported rendering, serialization, diagnostics, terminal
output, clipboard preparation, logs, reports or artifacts, as well as authorization bypasses and
capture-integrity failures. These architectural controls are not a claim of an independent security
certification; release qualification and dedicated restricted CI integration remain pending.

See [trust and policy](docs/cli.md#trust-and-executionpolicy),
[artifact sanitization](docs/artifacts-and-reporters.md), and
[application boundaries](docs/codebase/application-api-reference.md).
