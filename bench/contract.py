"""Consistency checks across Foresee's published agent-facing surfaces.

Each check compares two things Foresee publishes (the live MCP schema, the docs, the skills) and reports where they
disagree. A finding is an observation about the documents, not a claim about how the live server prices.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

from bench import dense
from bench.snapshot import Snapshot

OBSERVATIONS = Path(__file__).resolve().parent.parent / "contract" / "observations.json"
FENCE = re.compile(r"```(request|response)\n(.*?)```", re.DOTALL)
PROFILE_PATH = re.compile(r"\b(?:auto|property|prior_insurance)\.[a-z0-9_]+(?:\[\])?(?:\.[a-z0-9_]+)?")
SECTION_ENDS = {"docs.md": "## Tool 2:", "skill-website.md": "## Live quotes", "skill-repo.md": "## Live quotes"}
VERSION = re.compile(r"^version:\s*(\S+)", re.MULTILINE)


@dataclass
class Finding:
    id: str
    title: str
    detail: str
    evidence: list[dict] = field(default_factory=list)


def quote_section(snap: Snapshot, name: str) -> str:
    """The part of a document about `quote_insurance` (live_carrier_quotes has its own schema, not captured)."""
    return snap.text(name).split(SECTION_ENDS[name], 1)[0]


def examples(snap: Snapshot, kind: str) -> list[str]:
    return [body for found, body in FENCE.findall(quote_section(snap, "docs.md")) if found == kind]


class Schema:
    """Walks a JSON Schema with local `$ref`s, `anyOf` and arrays, by property path."""

    def __init__(self, root: dict):
        self.root = root

    def resolve(self, node: dict) -> list[dict]:
        if "$ref" in node:
            return self.resolve(self.root["$defs"][node["$ref"].rsplit("/", 1)[1]])
        found = [resolved for branch in node.get("anyOf", []) for resolved in self.resolve(branch)]
        if node.get("type") == "array" and "items" in node:
            found.extend(self.resolve(node["items"]))
        # A node can carry properties and an anyOf of `required` alternatives at once.
        if "properties" in node or isinstance(node.get("additionalProperties"), dict):
            found.append(node)
        return found or [node]

    def child(self, node: dict, name: str) -> list[dict]:
        found = []
        for resolved in self.resolve(node):
            if name in resolved.get("properties", {}):
                found.extend(self.resolve(resolved["properties"][name]))
            elif isinstance(resolved.get("additionalProperties"), dict):
                found.extend(self.resolve(resolved["additionalProperties"]))
        return found

    def has_path(self, start: dict, path: str) -> bool:
        nodes = [start]
        for part in path.replace("[]", "").split("."):
            nodes = [child for node in nodes for child in self.child(node, part)]
            if not nodes:
                return False
        return True

    def unnamed_keys(self, value, node: dict, path: str = "") -> list[str]:
        """Keys in *value* that no schema property names: the server carries them but never rates them."""
        if isinstance(value, list):
            return [k for item in value for k in self.unnamed_keys(item, node, f"{path}[]")]
        if not isinstance(value, dict):
            return []
        missing = []
        for key, item in value.items():
            children = self.child(node, key)
            if children:
                missing.extend(self.unnamed_keys(item, {"anyOf": children}, f"{path}.{key}".lstrip(".")))
            else:
                missing.append(f"{path}.{key}".lstrip("."))
        return missing


def check_example_requests(snap: Snapshot) -> list[Finding]:
    """Every documented request validates against the live input schema and names only rated fields."""
    schema = snap.quote_tool()["inputSchema"]
    validator, walker = Draft202012Validator(schema), Schema(schema)
    findings = []
    for number, body in enumerate(examples(snap, "request"), 1):
        request = json.loads(body)
        errors = [e.message for e in validator.iter_errors(request)]
        unnamed = walker.unnamed_keys(request, schema)
        if errors or unnamed:
            findings.append(
                Finding(
                    f"example-request-{number}",
                    f"Docs request example {number} uses fields the live schema doesn't name",
                    "; ".join(errors + [f"`{key}` is not a schema field" for key in unnamed]),
                    [{"file": "docs.md", "quote": body.strip()[:200]}],
                )
            )
    return findings


def advertised_paths(snap: Snapshot) -> dict[str, set[str]]:
    """Profile paths that Foresee's own text tells agents to send, mapped to where each appears."""
    places: dict[str, set[str]] = {}
    for name in ("docs.md", "skill-website.md", "skill-repo.md"):
        for path in PROFILE_PATH.findall(quote_section(snap, name)):
            places.setdefault(path, set()).add(name)
    for body in examples(snap, "response"):
        for line in dense.parse(body):
            for path in line.tighten_by + [a["field"] for a in line.assumptions]:
                places.setdefault(path, set()).add("docs.md response examples")
    return places


def check_advertised_paths(snap: Snapshot) -> list[Finding]:
    """Every field path the docs, skills or example responses name exists in the live profile schema."""
    schema = snap.quote_tool()["inputSchema"]
    walker, profile = Schema(schema), schema["properties"]["profile"]
    return [
        Finding(
            f"path-{path}",
            f"`{path}` is named in Foresee's guidance but not in the live input schema",
            "The live schema says a key it doesn't name is carried but never rated.",
            [{"file": place, "quote": path} for place in sorted(places)],
        )
        for path, places in sorted(advertised_paths(snap).items())
        if not walker.has_path(profile, path)
    ]


def check_coverage_sums(snap: Snapshot) -> list[Finding]:
    """Per-coverage monthly lines (`L`) add up to the carrier's monthly price (`C`)."""
    gaps = []
    for body in examples(snap, "response"):
        for line in dense.parse(body):
            for carrier in line.carriers:
                total = sum((amount for _, amount in carrier.coverages), Decimal(0))
                if total != carrier.monthly:
                    gaps.append(f"{line.line} {carrier.key}: C {carrier.monthly} vs L sum {total}")
    if not gaps:
        return []
    detail = "Likely per-line rounding; agents that add the L rows will not match the headline price."
    return [
        Finding(
            "coverage-sums",
            "Per-coverage lines differ from the monthly price by a cent",
            detail,
            [{"file": "docs.md response examples", "quote": gap} for gap in gaps],
        )
    ]


def check_skill_copies(snap: Snapshot) -> list[Finding]:
    """Two copies of the same skill with the same version label carry the same text."""
    website, repo = snap.text("skill-website.md"), snap.text("skill-repo.md")
    findings = []
    if website != repo and VERSION.search(website).group(1) == VERSION.search(repo).group(1):
        findings.append(
            Finding(
                "skill-version",
                f"Website and repository skills are both version {VERSION.search(repo).group(1)} but differ",
                f"{_changed_lines(website, repo)} lines differ; installed copies can't tell which text they have.",
                [
                    {"file": "skill-website.md", "quote": "version: " + VERSION.search(website).group(1)},
                    {"file": "skill-repo.md", "quote": "version: " + VERSION.search(repo).group(1)},
                ],
            )
        )
    digest = hashlib.sha256(website.encode()).hexdigest()
    index = json.dumps(snap.json("agent-skills-index.json"))
    if digest in index and hashlib.sha256(repo.encode()).hexdigest() not in index:
        findings.append(
            Finding(
                "skill-index",
                "The agent-skills index publishes the website skill's digest, not the repository's",
                "Agents that verify the digest get the website text, which lacks rules present in the repository copy.",
                [{"file": "agent-skills-index.json", "quote": digest}],
            )
        )
    return findings


def _changed_lines(a: str, b: str) -> int:
    return len(set(a.splitlines()) ^ set(b.splitlines()))


def normalise(text: str) -> str:
    return " ".join(text.split())


def check_observations(snap: Snapshot, path: Path = OBSERVATIONS) -> list[Finding]:
    """Hand-recorded pairs of statements; each stays a finding only while both quotes are still published."""
    findings = []
    for item in json.loads(path.read_text()):
        present = all(normalise(q["quote"]) in normalise(snap.searchable(q["file"])) for q in item["quotes"])
        if present:
            findings.append(Finding(item["id"], item["title"], item["detail"], item["quotes"]))
    return findings


CHECKS = (check_example_requests, check_advertised_paths, check_coverage_sums, check_skill_copies, check_observations)


def run(snap: Snapshot) -> list[Finding]:
    return [finding for check in CHECKS for finding in check(snap)]
