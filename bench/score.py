"""Score recorded replies offline and summarise them per condition, model and rule category."""

import json
from collections import defaultdict

from bench import rules, run, scenarios
from bench.snapshot import Snapshot


def reviews(name: str) -> dict[tuple[str, str], dict]:
    """Hand verdicts that overturn an automatic one, each with the reason (results/<run>/review.json)."""
    path = run.RESULTS / name / "review.json"
    return {(r["key"], r["check"]): r for r in json.loads(path.read_text())} if path.exists() else {}


def apply_reviews(key: str, outcomes: list[dict], reviewed: dict) -> list[dict]:
    for outcome in outcomes:
        verdict = reviewed.get((key, outcome["id"]))
        if verdict and verdict["passed"] != outcome["passed"]:
            outcome.update(automatic=outcome["passed"], passed=verdict["passed"], review=verdict["note"])
    return outcomes


def score_run(snap: Snapshot, name: str) -> list[dict]:
    built, reviewed = {s.id: s for s in scenarios.load(snap)}, reviews(name)
    scored = []
    records = run.recorded(run.RESULTS / name / "answers.jsonl")
    run.check_prompts(records, run.prompts(snap))
    for record in records:
        scenario = built[record["scenario"]]
        # Some checks apply only where the instructions describe the behaviour (e.g. consent text in the skills).
        applicable = [r for r in scenario.rules if record["condition"] in r.get("conditions", scenarios.CONDITIONS)]
        automatic = rules.score(record["reply"], scenario.context(), applicable)
        outcomes = apply_reviews(record["key"], automatic, reviewed)
        scored.append(
            {
                **{k: record[k] for k in ("key", "scenario", "condition", "model", "repeat")},
                "scenario_category": scenario.category,
                "origin": scenario.origin,
                "outcomes": outcomes,
                "all_passed": all(o["passed"] for o in outcomes),
            }
        )
    return scored


def rate(passed: int, total: int) -> dict:
    return {"passed": passed, "total": total, "rate": round(passed / total, 3) if total else None}


def group(scored: list[dict], fields: tuple[str, ...]) -> list[tuple[tuple, list[bool]]]:
    """Whether each reply passed all its checks, grouped by the given fields."""
    groups = defaultdict(list)
    for item in scored:
        groups[tuple(item[f] for f in fields)].append(item["all_passed"])
    return sorted(groups.items())


def summarise(scored: list[dict]) -> dict:
    by_cell, by_category, by_rule = defaultdict(list), defaultdict(list), defaultdict(list)
    for item in scored:
        cell = (item["condition"], item["model"])
        by_cell[cell].append(item["all_passed"])
        for outcome in item["outcomes"]:
            by_category[(*cell, outcome["category"])].append(outcome["passed"])
            by_rule[(item["scenario"], outcome["id"], item["condition"])].append(outcome["passed"])
    overturned = sum("automatic" in o for item in scored for o in item["outcomes"])
    return {
        "replies_by": {
            name: [{**dict(zip(fields, key, strict=True)), **rate(sum(v), len(v))} for key, v in group(scored, fields)]
            for name, fields in (
                ("condition", ("condition",)),
                ("model", ("model", "condition")),
                ("scenario_category", ("scenario_category", "condition")),
                ("origin", ("origin", "condition")),
            )
        },
        "replies": len(scored),
        "checks": sum(len(item["outcomes"]) for item in scored),
        "overturned_on_review": overturned,
        "replies_passing_all_selected_checks": [
            {"condition": c, "model": m, **rate(sum(v), len(v))} for (c, m), v in sorted(by_cell.items())
        ],
        "checks_by_category": [
            {"condition": c, "model": m, "category": cat, **rate(sum(v), len(v))}
            for (c, m, cat), v in sorted(by_category.items())
        ],
        "checks_by_rule": [
            {"scenario": s, "rule": r, "condition": c, **rate(sum(v), len(v))}
            for (s, r, c), v in sorted(by_rule.items())
        ],
    }


def write(snap: Snapshot, name: str) -> dict:
    scored = score_run(snap, name)
    summary = summarise(scored)
    folder = run.RESULTS / name
    (folder / "scores.jsonl").write_text("".join(json.dumps(item) + "\n" for item in scored))
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def table(summary: dict) -> str:
    rows = ["| Condition | Model | Replies passing all selected checks |", "|---|---|---|"]
    rows += [
        f"| {r['condition']} | {r['model']} | {r['passed']}/{r['total']} ({r['rate']:.0%}) |"
        for r in summary["replies_passing_all_selected_checks"]
    ]
    return "\n".join(rows)
