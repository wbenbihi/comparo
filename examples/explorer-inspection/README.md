# Explorer inspection examples

From the repository root, launch the workspace implementation explicitly:

```console
.venv/bin/comparo tui --config examples/explorer-inspection/comparo.yaml
```

A bare `comparo` may select a separately installed pipx release instead of this checkout.
Quit any already running instance before relaunching; Python source changes are not hot-reloaded.

- **Value → Standalone value with masked secret → r**: public nested values and two masked secrets, without selecting a consumer.
- **Matrix → Matrix without consumers → r**: generated region/tier cases and their values; no execution plan.
- **Request → Nested JSON body → r → Body**: JSON document, array/object navigation and focused-path inspector. Select `payment.token` to check masking and declaration provenance.
- **Schema → deep-order**: required `items → 0..n → sku`, `customer` and `owner` references to the separate customer schema, and recursive `$defs/node` references. References open the corresponding declaration in the tree. Cycle branches stop explicitly.

These are inspection fixtures. The loopback URL intentionally has no demo server; inspection does not send requests. The token is an artificial masking canary, not a credential.
