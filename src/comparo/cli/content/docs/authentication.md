# Authentication

Reference credentials from environment variables instead of putting token literals
in YAML or command arguments. This complete Request can be added to a starter
project in `.comparo/requests/account.yaml`:

```yaml
apiVersion: comparo/v1alpha1
kind: Request
metadata:
  id: account
  name: Authenticated account
spec:
  request:
    method: GET
    endpoint: /account
    auth:
      type: bearer
      token:
        $env: API_TOKEN
```

Supply `API_TOKEN` in your process environment using your existing credential
workflow. The configuration contains the variable name, not its value.

```console
comparo validate
comparo plan --subject account
comparo render --subject account --format json
comparo run --subject account --env baseline
```

Validation and planning do not resolve environment variables. Rendering and execution
require the token to be available. Rendering prints a sanitized preview and sends
no HTTP requests. To use different credentials per environment, use Environment
inputs and the `$secret` namespace; consult the installed configuration schema.

Without `--policy`, local execution uses the permissive trusted-local authority.
An explicit operator-owned ExecutionPolicy must authorize both the ambient variable
and credential egress to the intended destination. The Project cannot grant that
authority. Do not generate a permissive policy just to make an authorization error
disappear. Read `comparo docs limitations` for execution boundaries.
