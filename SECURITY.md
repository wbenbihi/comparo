# Security Policy

comparo handles credentials and can capture HTTP responses, so security reports are taken
seriously.

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Instead, use GitHub's
[private vulnerability reporting](https://github.com/wbenbihi/comparo/security/advisories/new)
for this repository. You will receive an acknowledgement, and we will coordinate a fix and
disclosure timeline with you.

## Handling of secrets

comparo is designed so that secret values never leak:

- Secrets are resolved lazily and tracked by provenance; any value originating from a secret
  is masked in the TUI and scrubbed from reports and snapshots.
- Recorded responses and diff artifacts are scrubbed of secret-tainted values before they are
  written.

If you find a way to make a secret leak into terminal output, logs, reports, snapshots, or
version control, please report it as a vulnerability.
