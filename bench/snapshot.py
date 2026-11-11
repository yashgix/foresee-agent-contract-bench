"""A dated, hashed capture of Foresee's public agent-facing surfaces."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "snapshot" / "2026-11-10"


@dataclass
class Snapshot:
    root: Path
    manifest: dict

    def text(self, name: str) -> str:
        return (self.root / name).read_text()

    def json(self, name: str):
        return json.loads(self.text(name))

    def searchable(self, name: str) -> str:
        """The file's prose: for JSON, every string value joined, so quotes match without escapes."""
        if not name.endswith(".json"):
            return self.text(name)
        return " ".join(_strings(self.json(name)))

    def source(self, name: str) -> str:
        return self.manifest["files"][name]["source"]

    def quote_tool(self) -> dict:
        tools = self.json("tools-list.json")["result"]["tools"]
        return next(tool for tool in tools if tool["name"] == "quote_insurance")

    def server_instructions(self) -> str:
        return self.json("initialize.json")["result"]["instructions"]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(root: Path = DEFAULT) -> Snapshot:
    """Load a snapshot and refuse it if any file differs from its recorded hash."""
    manifest = json.loads((root / "MANIFEST.json").read_text())
    for name, entry in manifest["files"].items():
        if sha256(root / name) != entry["sha256"]:
            raise ValueError(f"{name} does not match its recorded sha256")
    return Snapshot(root, manifest)
