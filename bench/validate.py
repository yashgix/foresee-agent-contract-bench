"""Compare the deterministic grader with hand labels on a sample of recorded checks.

`results/<run>/labels.jsonl` holds one line per hand-reviewed check: {"key", "check", "human": true|false, "note"}.
The grader's verdict for the same check comes from scores.jsonl, so the comparison is reproducible.
"""

import random
from collections import Counter

from bench import run


def sample(name: str, per_rule: int, seed: int = 11) -> list[dict]:
    """A reproducible sample of passing checks per rule kind, plus every failing check."""
    scored = run.recorded(run.RESULTS / name / "scores.jsonl")
    checks = [
        {"key": item["key"], "check": o["id"], "category": o["category"], "passed": o.get("automatic", o["passed"])}
        for item in scored
        for o in item["outcomes"]
    ]
    failing = [c for c in checks if not c["passed"]]
    rng, picked = random.Random(seed), []
    for category in sorted({c["category"] for c in checks}):
        passing = [c for c in checks if c["passed"] and c["category"] == category]
        picked += rng.sample(passing, min(per_rule, len(passing)))
    return failing + picked


def agreement(name: str) -> dict:
    labels = {(r["key"], r["check"]): r["human"] for r in run.recorded(run.RESULTS / name / "labels.jsonl")}
    verdicts = {
        (item["key"], o["id"]): o.get("automatic", o["passed"])  # the grader's own verdict, before hand review
        for item in run.recorded(run.RESULTS / name / "scores.jsonl")
        for o in item["outcomes"]
    }
    pairs = Counter((verdicts[k], human) for k, human in labels.items())
    return {
        "labelled": len(labels),
        "grader_fail_human_fail": pairs[(False, False)],
        "grader_fail_human_pass": pairs[(False, True)],  # false positives
        "grader_pass_human_pass": pairs[(True, True)],
        "grader_pass_human_fail": pairs[(True, False)],  # false negatives
    }
