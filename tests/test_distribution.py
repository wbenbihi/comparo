"""Guards on the published distribution: dependencies and the exported schema.

The dev environment always carries the dev group's transitive dependencies, so
an undeclared runtime import works locally and in CI yet crashes a plain
``pip install comparo``. These tests pin the distribution's contract instead:
every third-party import in ``src/`` must be satisfiable by the declared
runtime dependencies, and the committed JSON Schema must match the generated
one, so neither can drift silently again.
"""

import ast
import json
import sys
import tomllib
from pathlib import Path

import jsonschema
from ruamel.yaml import YAML

from comparo.core.loader import load_project
from comparo.core.schema import json_schema

ROOT = Path(__file__).parent.parent

#: Third-party imports that are intentionally not declared, each with a reason.
#: An entry here must name a package guaranteed by a *declared* dependency.
ALLOWED_UNDECLARED = {
    "rich": "a required dependency of textual, which is declared",
}


def _declared_dependencies() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for requirement in pyproject["project"]["dependencies"]:
        name = requirement.split(">")[0].split("=")[0].split("<")[0].split("[")[0].strip()
        names.add(name.lower().replace("-", "_"))
    return names


def _imported_top_level_names() -> set[str]:
    names: set[str] = set()
    for file in (ROOT / "src" / "comparo").rglob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def test_every_runtime_import_is_a_declared_dependency() -> None:
    # Would have caught: `packaging` imported by adapters/updates.py while only
    # present transitively via the dev group — a crash on `pip install comparo`.
    declared = _declared_dependencies()
    third_party = {
        name
        for name in _imported_top_level_names()
        if name not in sys.stdlib_module_names
        and name != "comparo"
        and name not in ALLOWED_UNDECLARED
    }
    undeclared = {
        name
        for name in third_party
        if name.lower().replace("-", "_").replace(".", "_")
        not in {d.replace(".", "_") for d in declared}
        # `ruamel.yaml` is declared with a dot; its import name is `ruamel`.
        and not any(d.startswith(f"{name}.") or d.startswith(f"{name}_") for d in declared)
    }
    assert not undeclared, (
        f"imported in src/ but missing from [project.dependencies]: {sorted(undeclared)} — "
        "a plain `pip install comparo` would crash on these imports"
    )


def test_committed_schema_matches_the_generated_schema() -> None:
    # `comparo schema` docs promise the schema "never drifts from the real
    # config"; this holds that promise for the committed copy editors fetch.
    committed = json.loads((ROOT / "schema" / "comparo-v1.schema.json").read_text(encoding="utf-8"))
    assert committed == json_schema(), (
        "schema/comparo-v1.schema.json is stale — regenerate with "
        "`comparo schema --output schema/comparo-v1.schema.json`"
    )


def test_every_example_project_loads_and_validates_against_the_schema() -> None:
    # The examples are the first thing a visitor copies; each must satisfy both
    # validators — the loader (msgspec) and the published JSON Schema — so the
    # two config surfaces cannot drift apart unnoticed.
    schema = json_schema()
    validator = jsonschema.Draft202012Validator(schema)
    yaml = YAML(typ="safe")
    examples = sorted((ROOT / "examples").iterdir())
    assert examples, "examples/ directory is empty"
    for example in examples:
        if not example.is_dir() or example.name == "broken-project":
            continue
        manifest = example / "comparo.yaml"
        source = manifest if manifest.exists() else example
        loaded = load_project(source)
        assert loaded.objects, f"{example.name} loaded no objects"
        for file in sorted(example.rglob("*.yaml")):
            document = yaml.load(file.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or "apiVersion" not in document:
                continue
            errors = sorted(validator.iter_errors(document), key=str)
            assert not errors, f"{file.relative_to(ROOT)}: {errors[0].message}"


def test_the_broken_example_produces_every_documented_diagnostic() -> None:
    # broken-project's README promises a menu of six distinct diagnostics; each
    # is a loader behavior worth pinning — including the near-miss suggestions.
    import pytest

    from comparo.core.diagnostics import LoadError

    with pytest.raises(LoadError) as caught:
        load_project(ROOT / "examples" / "broken-project" / "comparo.yaml")
    rendered = "\n".join(
        diagnostic.render(caught.value.root) for diagnostic in caught.value.diagnostics
    )
    for promised in (
        "invalid YAML",
        "methdo",  # "unknown field `methdo`" — msgspec quotes with backticks
        "missing metadata.id",
        "duplicate id 'request.ping'",
        "did you mean 'schema.order'?",
        "same segments, different order",
    ):
        assert promised in rendered, f"documented diagnostic not produced: {promised!r}"
