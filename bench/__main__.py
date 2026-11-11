"""Command line: `python -m bench contract | eval | score`."""

import argparse
import json
from dataclasses import asdict

from bench import contract, regression, run, scenarios, score, snapshot


def parse_models(text: str) -> dict[str, int]:
    """'claude-haiku-5-5:2,claude-sonnet-5-5:1' -> {model: repeats}."""
    return {name: int(n) for name, n in (item.split(":") for item in text.split(","))}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bench")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("contract", help="check Foresee's published surfaces against each other")
    evaluate = commands.add_parser("eval", help="record replies for every scenario x condition x model")
    evaluate.add_argument("run")
    evaluate.add_argument("--models", type=parse_models, default=parse_models("claude-haiku-5-5:1"))
    evaluate.add_argument("--max-calls", type=int, default=10)
    evaluate.add_argument("--stop-at", type=float, default=95.0, help="weekly subscription use, percent")
    evaluate.add_argument("--workers", type=int, default=4)
    evaluate.add_argument("--scenario", action="append", help="limit to these scenario ids")
    evaluate.add_argument("--reuse", help="first copy replies with unchanged prompts from this run")
    commands.add_parser("regression-demo", help="show the checks catch a schema rename")
    rescore = commands.add_parser("score", help="score a recorded run offline")
    rescore.add_argument("run")
    args = parser.parse_args(argv)

    snap = snapshot.load()
    if args.command == "contract":
        findings = [asdict(f) for f in contract.run(snap)]
        (run.RESULTS / "contract.json").write_text(json.dumps(findings, indent=2) + "\n")
        for finding in findings:
            print(f"- {finding['title']}")
        print(f"{len(findings)} findings -> results/contract.json")
    elif args.command == "regression-demo":
        print(json.dumps(regression.demo(), indent=2))
    elif args.command == "eval":
        ids = args.scenario or [s.id for s in scenarios.load(snap)]
        jobs = run.plan(ids, args.models)
        if args.reuse:
            print(f"reused {run.reuse(snap, args.run, args.reuse)} replies with unchanged prompts")
        print(json.dumps(run.execute(snap, args.run, jobs, run.Budget(args.max_calls, args.stop_at), args.workers)))
    else:
        print(score.table(score.write(snap, args.run)))


if __name__ == "__main__":
    main()
