"""Stamp immutable build provenance into both distributions without changing source files."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Keep the source revision when rebuilding a wheel from an sdist without Git."""

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        """Force-include a temporary stamp; editable installs read their checkout instead."""
        if version == "editable":
            return
        root = Path(self.root)
        sys.path.insert(0, str(root / "src"))
        try:
            from comparo.build_info import recorded_revision
            from comparo.build_info import source_revision

            sha, modified = source_revision(root)
            if sha is None:
                sha, modified = recorded_revision(root / "src" / "comparo")
        finally:
            sys.path.pop(0)
        self._temporary = TemporaryDirectory(prefix="comparo-build-")
        stamp = Path(self._temporary.name) / "_build_info.json"
        stamp.write_text(json.dumps({"sha": sha, "modified": modified}) + "\n")
        destination = "comparo/_build_info.json"
        if self.target_name == "sdist":
            destination = "src/" + destination
        cast(dict[str, str], build_data["force_include"])[str(stamp)] = destination

    def finalize(self, version: str, build_data: dict[str, object], artifact_path: str) -> None:
        """Remove temporary build metadata after packaging."""
        if version != "editable":
            self._temporary.cleanup()
