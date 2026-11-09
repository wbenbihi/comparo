# Project layout and configuration

The current configuration lineage is `comparo/v1alpha1`. A project has exactly one
Project document, first in `comparo.yaml`. Other resources can follow as YAML
documents separated by `---`, or live in the Project's data directory.

```yaml
apiVersion: comparo/v1alpha1
kind: Project
metadata:
  id: my-api
  name: My API
spec:
  data: .comparo
```

`comparo init` puts every starter resource in the manifest. `comparo init --layout split`
creates this layout:

```text
comparo.yaml
.comparo/
  environments.yaml
  requests/
    health.yaml
  profiles/
    checks.yaml
```

The manifest contains discovery and defaults; the other files contain Environments,
Requests, and profiles. To split an existing project, create `.comparo/` and move
resource documents out of the manifest. Do not keep duplicate definitions.

Resources use `apiVersion`, `kind`, `metadata.id`, `metadata.name`, and `spec`.
References use `$use`, not `$ref`. For subject references, qualify the kind:
`{$use: Request/health}`. Environment references can use `{$use: baseline}`.
Identifiers are lowercase alphanumeric slugs with hyphens.

Project discovery includes YAML under `spec.data`, configured sources, and explicit
`--source/-S` paths. `--config/-C` accepts a manifest or directory. Keep credentials
out of resource files; see `comparo docs authentication`.

```console
comparo schema config
comparo validate --config comparo.yaml --format json
comparo plan --config comparo.yaml --format json
```

The installed schema gives exact field shapes. The compiler also checks references
and semantics. YAML anchors, aliases, duplicate keys, and custom tags are rejected.
See `comparo docs requests` and `comparo docs limitations`.
