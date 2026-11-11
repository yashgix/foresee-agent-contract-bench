"""Fill docs/template.html with figures computed from the recorded results, writing docs/index.html.

Every number on the page comes from results/<run> (scores, summary, labels) and results/contract.json, so the page
can't drift from the data. Placeholders look like {{name}}; an unknown or unused name is an error.

    python scripts/build_site.py v3
"""

import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from bench.validate import agreement  # noqa: E402

CONDITIONS = ("no-instructions", "tool-only", "website-skill", "repo-skill")


def load(path: Path):
    text = path.read_text()
    return [json.loads(line) for line in text.splitlines()] if path.suffix == ".jsonl" else json.loads(text)


def figures(run: str) -> dict[str, str]:
    folder = REPO / "results" / run
    scored, summary = load(folder / "scores.jsonl"), load(folder / "summary.json")
    specs = load(REPO / "scenarios" / "scenarios.json")
    contract = load(REPO / "results" / "contract.json")
    out: dict[str, str] = {
        "replies": str(summary["replies"]),
        "checks": str(summary["checks"]),
        "scenarios": str(len(specs)),
        "published_scenarios": str(sum(s["origin"] == "published" for s in specs)),
        "variant_scenarios": str(sum(s["origin"] == "variant" for s in specs)),
        "published_results": str(len({s["base"] for s in specs})),
        "observations": str(len(contract)),
        "failing_checks": str(sum(not o["passed"] for i in scored for o in i["outcomes"])),
        "hand_verdicts": str(summary["overturned_on_review"]),
        "categories": str(len({s["category"] for s in specs})),
    }
    per_condition = Counter(i["condition"] for i in scored)
    out["per_condition"] = str(per_condition[CONDITIONS[0]])
    for c in CONDITIONS:
        key = c.replace("-", "_")
        items = [i for i in scored if i["condition"] == c]
        passed = sum(i["all_passed"] for i in items)
        out[f"pass_{key}"] = str(passed)
        out[f"pct_{key}"] = f"{100 * passed / len(items):.1f}"
        fails = [(i, o) for i in items for o in i["outcomes"] if not o["passed"]]
        out[f"units_{key}"] = str(sum("liability limit" in o["evidence"] for _, o in fails))
        out[f"derived_{key}"] = str(
            len({i["key"] for i, o in fails if o["id"] == "numbers" and "liability limit" not in o["evidence"]})
        )
        out[f"guess_{key}"] = str(sum(o["id"].endswith("no-guess") for _, o in fails))
        out[f"estimate_{key}"] = str(sum(o["id"] == "estimate" for _, o in fails))
    out["estimate_checked"] = str(sum(o["id"] == "estimate" for i in scored for o in i["outcomes"]))
    out["estimate_missed"] = str(sum(int(out[f"estimate_{c.replace('-', '_')}"]) for c in CONDITIONS))
    for name, field in (("category", "scenario_category"), ("model", "model"), ("origin", "origin")):
        out[f"table_{name}"] = table(scored, field)
    out.update(validation(run))
    out["examples"] = examples(folder)
    out["observation_list"] = observation_list(contract)
    out["rule_tests"], out["tests"] = test_counts()
    return out


def examples(folder: Path) -> str:
    """Pairs of verbatim excerpts; each part must appear in the recorded reply (whitespace aside)."""
    replies = {r["key"]: " ".join(r["reply"].split()) for r in load(folder / "answers.jsonl")}
    blocks = []
    for pair in load(REPO / "docs" / "examples.json"):
        cells = []
        for side in ("lost", "kept"):
            item = pair[side]
            for part in item["parts"]:
                if " ".join(part.split()) not in replies[item["key"]]:
                    raise SystemExit(f"excerpt not found in {item['key']}: {part[:40]!r}")
            quote = " … ".join(html.escape(p) for p in item["parts"])
            word = "Lost" if side == "lost" else "Kept"
            cells.append(
                f'<div class="reply {side}"><div class="who">{word} · {item["label"]}</div>'
                f'<p>{quote}</p><p class="note">{html.escape(item["note"])}</p></div>'
            )
        blocks.append(
            f'<div class="stack" style="gap:0.6rem"><p class="ask"><b>Asked:</b> “{html.escape(pair["question"])}”</p>'
            f'<div class="pair">{"".join(cells)}</div></div>'
        )
    return "\n".join(blocks)


def observation_list(contract: list[dict]) -> str:
    def code(text: str) -> str:
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text))

    items = "".join(f"<li><b>{code(f['title'])}.</b> {code(f['detail'])}</li>" for f in contract)
    return f'<ol class="list small">{items}</ol>'


def test_counts() -> tuple[str, str]:
    """Collected tests in tests/test_rules.py and in total (needs the snapshot)."""

    def count(target: str) -> str:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", target],
            capture_output=True,
            text=True,
            cwd=REPO,
            check=True,
        )
        return str(sum(int(n) for n in re.findall(r": (\d+)$", done.stdout, re.MULTILINE)))

    return count("tests/test_rules.py"), count("tests")


def table(scored: list[dict], field: str) -> str:
    """Rows of a results table: replies passing all selected checks, per group and condition."""
    groups: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for item in scored:
        groups[item[field]][item["condition"]].append(item["all_passed"])
    rows = []
    for name in sorted(groups):
        cells = "".join(f'<td class="num">{sum(groups[name][c])}/{len(groups[name][c])}</td>' for c in CONDITIONS)
        label = name.replace("-5-5", " 5.5").replace("-", " ").title() if field == "model" else name.capitalize()
        rows.append(f"<tr><td>{label}</td>{cells}</tr>")
    return "\n".join(rows)


def validation(run: str) -> dict[str, str]:
    if not (REPO / "results" / run / "labels.jsonl").exists():
        return {}
    found = agreement(run)
    return {
        "labelled": str(found["labelled"]),
        "true_fail": str(found["grader_fail_human_fail"]),
        "false_fail": str(found["grader_fail_human_pass"]),
        "true_pass": str(found["grader_pass_human_pass"]),
        "false_pass": str(found["grader_pass_human_fail"]),
    }


def main() -> int:
    run = sys.argv[1] if len(sys.argv) > 1 else "v3"
    values = figures(run)
    template = (REPO / "docs" / "template.html").read_text()
    used = set(re.findall(r"\{\{(\w+)\}\}", template))
    missing = used - values.keys()
    if missing:
        raise SystemExit(f"no value for: {', '.join(sorted(missing))}")
    page = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)
    (REPO / "docs" / "index.html").write_text(page)
    print(f"docs/index.html built from results/{run}: {len(used)} figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
