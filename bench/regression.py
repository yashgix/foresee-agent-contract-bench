"""Show that the contract checks catch drift: rename a schema field the docs still advertise, then fix the docs."""

import json
import shutil
import tempfile
from pathlib import Path

from bench import contract, snapshot

OLD, NEW = "garaging_zip", "garage_zip"


def edited(root: Path, changes: dict[str, tuple[str, str]]) -> snapshot.Snapshot:
    """Edit files in *root* (old -> new) and update their manifest hashes."""
    manifest = json.loads((root / "MANIFEST.json").read_text())
    for name, (old, new) in changes.items():
        text = (root / name).read_text()
        if old not in text:
            raise ValueError(f"{old!r} is not in {name}")
        (root / name).write_text(text.replace(old, new))
        manifest["files"][name]["sha256"] = snapshot.sha256(root / name)
    (root / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    return snapshot.load(root)


def ids(snap: snapshot.Snapshot) -> set[str]:
    return {finding.id for finding in contract.run(snap)}


def demo(source: Path = snapshot.DEFAULT) -> dict:
    """Findings before, after the server-side rename, and after the docs are updated to match."""
    with tempfile.TemporaryDirectory() as work:
        base = Path(work) / "snapshot"
        shutil.copytree(source, base)
        before = ids(snapshot.load(base))
        broken = ids(edited(base, {"tools-list.json": (OLD, NEW)}))
        fixed = ids(edited(base, {"docs.md": (OLD, NEW)}))
    return {
        "change": f"server renames auto.vehicles[].{OLD} to {NEW}; docs unchanged",
        "new_findings": sorted(broken - before),
        "after_docs_fix": sorted(fixed - before),
        "detected_and_cleared": bool(broken - before) and not (fixed - before),
    }
