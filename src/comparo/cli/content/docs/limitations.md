# Executable limitations

Comparo is alpha software. Its configuration schema describes some capabilities
that the installed runtime cannot execute. Successful validation means the project
compiled; it does not establish that every declaration is executable.

- `run`, `diff`, and `exec` execute Request subjects, HTTP inputs, assertions,
  comparisons, gates, and supported runtime profiles.
- Ordinary HTTP and SSE responses are supported. Stateful Scenario execution is
  not implemented, even though Scenario resources can be represented and inspected.
- WebSocket, gRPC, browser automation, and LLM-based judges are not executable features.
- Local file and artifact adapters require POSIX descriptor APIs; runtime use is
  supported on Linux and macOS. Some offline commands are platform-neutral.
- `validate` and `plan` do not resolve credentials or make HTTP requests. `render`
  resolves local inputs and returns sanitized previews without HTTP.
- Omitting `--policy` uses permissive trusted-local authority. For an untrusted
  project or CI checkout, supply an operator-owned ExecutionPolicy from outside
  project discovery. A Project cannot grant itself authority.
- Mutation permission and explicit mutation approval are separate. Initialization
  and planning do not approve HTTP mutations.
- Remote response schemas are bounded and policy-controlled; external nested schema
  references are denied. Unsupported capabilities fail before transport.

Use `comparo COMMAND --help`, `comparo schema config`, and `comparo schema policy`
for exact installed options and schemas. Consult `comparo docs comparisons` for exit
codes and `comparo docs authentication` for credential references.
