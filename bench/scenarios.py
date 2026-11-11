"""Scenarios built from Foresee's published examples, and the prompts an assistant sees for each condition."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from bench import contract, dense
from bench.snapshot import Snapshot

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios" / "scenarios.json"
CONDITIONS = {
    "no-instructions": None,
    "tool-only": None,
    "website-skill": "skill-website.md",
    "repo-skill": "skill-repo.md",
}
BARE = "You are a helpful AI assistant. The user has connected an insurance quoting tool."
FRAME = (
    "You are a helpful AI assistant. The user has connected Foresee's insurance tools. Foresee's server "
    "instructions and the quote_insurance tool description follow, exactly as Foresee publishes them."
)


@dataclass
class Scenario:
    id: str
    title: str
    origin: str
    category: str
    user: str
    arguments: dict
    result: dict
    rules: list[dict]

    def context(self) -> dict:
        return {"result": self.result, "result_text": json.dumps(self.result), "user": self.user}


def apply_edits(text: str, edits: list[dict]) -> str:
    rows = text.strip().splitlines()
    for edit in edits:
        if "drop" in edit:
            rows = _drop_carrier(rows, edit["drop"])
        elif "find" in edit:
            joined = "\n".join(rows)
            if joined.count(edit["find"]) != 1:
                raise ValueError(f"edit target must occur once: {edit['find'][:50]!r}")
            rows = joined.replace(edit["find"], edit["replace"]).splitlines()
        else:
            hits = [i for i, row in enumerate(rows) if row.startswith(edit["after"])]
            if len(hits) != 1:
                raise ValueError(f"insert anchor must match one row: {edit['after']!r}")
            rows[hits[0] + 1 : hits[0] + 1] = edit["insert"]
    return "\n".join(rows)


def _drop_carrier(rows: list[str], key: str) -> list[str]:
    """Remove a carrier's C row and the W, L and D rows under it."""
    start = next(i for i, row in enumerate(rows) if row.startswith(f"C {key}|"))
    end = start + 1
    while end < len(rows) and rows[end][:2] in ("W ", "L ", "D "):
        end += 1
    return rows[:start] + rows[end:]


def merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        base[key] = merge(base.get(key, {}), value) if isinstance(value, dict) else value
    return base


def build(snap: Snapshot, spec: dict) -> Scenario:
    request = merge(json.loads(contract.examples(snap, "request")[spec["base"] - 1]), spec.get("arguments", {}))
    request["carriers"] = request.get("carriers", []) + spec.get("add_carriers", [])
    text = apply_edits(contract.examples(snap, "response")[spec["base"] - 1], spec["edits"])
    result = dense.structured(text)
    return Scenario(
        spec["id"], spec["title"], spec["origin"], spec["category"], spec["user"], request, result, spec["rules"]
    )


def load(snap: Snapshot, path: Path = SCENARIOS) -> list[Scenario]:
    return [build(snap, spec) for spec in json.loads(path.read_text())]


def system_prompt(snap: Snapshot, condition: str) -> str:
    if condition == "no-instructions":
        return BARE
    tool = snap.quote_tool()
    parts = [
        FRAME,
        f"<server_instructions>\n{snap.server_instructions()}\n</server_instructions>",
        f'<tool name="quote_insurance">\n{tool["description"]}\n</tool>',
    ]
    if CONDITIONS[condition]:
        parts.append(f"<installed_skill>\n{snap.text(CONDITIONS[condition])}\n</installed_skill>")
    return "\n\n".join(parts)


def user_prompt(scenario: Scenario) -> str:
    return (
        f"User: {scenario.user}\n\n"
        f"You called quote_insurance with:\n{json.dumps(scenario.arguments, indent=2)}\n\n"
        f"It returned this structuredContent:\n{json.dumps(scenario.result, indent=2)}\n\n"
        "Write your reply to the user now. You cannot call any more tools."
    )


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n\x00\n{user}".encode()).hexdigest()
