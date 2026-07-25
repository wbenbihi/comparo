# Quickstart

This is the smallest complete Comparo project. Both Environments return the same status and the
same health payload, so its default comparison passes.

Start the shared server from the repository root:

```console
uv run --frozen python examples/support/demo_server.py
```

Then open the TUI:

```console
uv run --frozen comparo tui --config examples/quickstart/comparo.yaml
```

Suggested journey:

1. Open **Explorer** and inspect the two Environments, the `health` Request, and each profile.
2. Open **Compare** in Differences mode. Use `health`, baseline `baseline`, and candidate
   `candidate` if the fields are not already populated.
3. Press `p` to inspect the admitted plan, then `x` to execute it.
4. Open **Report** and refresh to reopen the persisted result without another HTTP request.
5. Try Execution profile mode with `compare-health` and `check-baseline`.

Headless comparison:

```console
uv run --frozen comparo diff --config examples/quickstart/comparo.yaml
```
