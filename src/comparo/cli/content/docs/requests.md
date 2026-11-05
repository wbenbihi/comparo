# HTTP Requests

Put this resource in `.comparo/requests/users.yaml` in a starter project. In a
single-file project, you can append it to `comparo.yaml` after `---` instead.

```yaml
apiVersion: comparo/v1alpha1
kind: Request
metadata:
  id: list-users
  name: List users
spec:
  request:
    method: GET
    endpoint: /users
    query:
      limit: "10"
    headers:
      Accept: application/json
```

`endpoint` is relative to the selected Environment's `baseUrl`. An absolute `url`
is also supported. Define the request under `spec.request`, not directly under
`spec`. Query and header values above are strings.

Select the new Request explicitly, or update the Project defaults:

```console
comparo validate
comparo plan --subject list-users
comparo render --subject list-users --format json
comparo run --subject list-users --env baseline
comparo diff --subject list-users --baseline baseline --candidate candidate
```

`render` resolves local inputs and produces a sanitized preview without HTTP;
`run` and `diff` execute requests. JSON, text, form, multipart, and bytes request
bodies are supported: consult `comparo schema config` for their exact typed shapes.
Build requests from the API's documented contract or implementation rather than
inventing fields, credentials, or success expectations.

See `comparo docs authentication`, `comparo docs assertions`, and `comparo docs comparisons`.
