# AI-assisted project authoring

Comparo includes a reusable `comparo` skill for agents such as Codex. The skill
teaches project authoring through the installed CLI, its documentation, and schema.
It does not require an AI API key or an AI service inside Comparo.

Install the skill in your API repository:

```console
comparo skill install
```

This creates `.agents/skills/comparo/SKILL.md`. To make it available across your
repositories, use `comparo skill install --user`, which installs under
`~/.agents/skills/comparo/`. `comparo skill show` prints the instructions before
installation. Installation preserves an existing different skill.

In Codex, try:

> Use $comparo to initialize comparisons for this API repository. Read its OpenAPI
> specification and route tests, create local and staging environments, organize
> requests under .comparo/requests/, and validate and plan without sending requests.

Provide environment URLs and relevant API sources when they are not available in
the repository. The skill uses those sources to choose requests and assertions,
references credentials by name, and reports assumptions and missing inputs.

An agent can also use the CLI directly:

```console
comparo --version
comparo docs --format json
comparo docs configuration --format markdown
comparo schema config
comparo init --layout split
comparo validate --format json
comparo plan --format json
```

Run `init` only for a new project. Existing projects should be edited in place.
Validation and planning are offline; `render` resolves local values, and execution
commands send HTTP requests. A request to initialize configuration alone does not
ask the agent to execute its requests.

These docs and the skill are bundled with the installed Comparo version. The skill
consults `comparo docs limitations` before selecting executable features. If a new
skill is not discovered, restart your agent session. A short project-specific note
in your own `AGENTS.md` can record the relevant environments and validation commands;
skill installation does not modify that file.
