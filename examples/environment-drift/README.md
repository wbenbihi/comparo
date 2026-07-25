# Environment drift

This project intentionally creates a mixed result set:

- `health` passes in both Environments;
- `release` has the same status but different JSON content;
- `whoami` demonstrates isolated `$var` and `$env` resolution and therefore differs by design;
- `degraded` returns HTTP 200 in the baseline and HTTP 503 in the candidate.

Start the shared server and launch the TUI:

```console
uv run --frozen python examples/support/demo_server.py
uv run --frozen comparo tui --config examples/environment-drift/comparo.yaml
```

Run the two useful Execution profiles from Compare:

- `baseline-smoke` should pass and demonstrates an assertion-only topology;
- `release-gate` should fail and populate the result surface with successful assertions, failed
  assertions, clean status diffs, and body/status drift.

After each execution, visit Report and refresh. Reports are authenticated persisted artifacts;
opening one does not resend the Requests.

The dotenv-style files are deliberately named `baseline.values` and `candidate.values`. They hold
non-secret demonstration labels, are declared by their respective Environment, and have no effect
unless the Request explicitly uses `$env: DEMO_LABEL`.

The headless release-gate command is expected to exit with code `1` because drift is deliberate:

```console
uv run --frozen comparo exec release-gate \
  --config examples/environment-drift/comparo.yaml
```
