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

1. Open **Explore** and inspect the two Environments, the `health` Request, and each profile.
2. Open **Launch → Run requests** for an environment comparison. Use `health`, baseline `baseline`, and candidate
   `candidate` if the fields are not already populated.
3. Press Ctrl+R to inspect the admitted plan, then Ctrl+X to execute it. Open Analyze results
   explicitly after the Run finishes; use `i` for captured response details.
4. Open **Reports** and refresh to reopen the persisted result without another HTTP request.
5. Try **Run an execution profile** with `compare-health` and `check-baseline`, or save a manual
   target with **Save as profile** after reviewing the YAML.
6. Open **Settings** to change preferences or remap the default keys.

Headless comparison:

```console
uv run --frozen comparo diff --config examples/quickstart/comparo.yaml
```
